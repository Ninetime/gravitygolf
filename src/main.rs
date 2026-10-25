use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::{Context, Result};
use chrono::Utc;
use openssl::ssl::{SslAcceptor, SslFiletype, SslMethod};
use serde::{Deserialize, Serialize};
use base64::{engine::general_purpose, Engine as _};
use tokio::process::Command;
use tokio::fs;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tracing::{error, info};
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;
use std::path::{Path, Component};

// --- Data Structures  ---

#[derive(Debug, Serialize, Deserialize, Clone)]
struct RequestLog {
    timestamp: String,
    remote_addr: String,
    protocol: String,
    tls_version: Option<String>,
    cipher_suite: Option<String>,
    method: String,
    path: String,
    query_string: String,
    http_version: String,
    headers: HashMap<String, String>,
    body_size: usize,
    body_preview: String,
    raw_request: String,
    processing_time_ms: u128,
}

#[derive(Debug, Clone)]
struct RequestInfo {
    method: String,
    path: String,
    query: String,
    version: String,
    headers: HashMap<String, String>,
    body: Vec<u8>,
}

impl RequestInfo {
    fn new() -> Self {
        Self {
            method: String::new(),
            path: String::new(),
            query: String::new(),
            version: String::new(),
            headers: HashMap::new(),
            body: Vec::new(),
        }
    }
}

// --- OpenSSL Setup ---

fn create_openssl_acceptor(cert_path: &PathBuf, key_path: &PathBuf) -> Result<SslAcceptor> {
    let mut builder = SslAcceptor::mozilla_intermediate(SslMethod::tls())
        .context("Failed to create SSL acceptor builder")?;
    
    builder.set_security_level(0);

    builder.set_certificate_file(cert_path, SslFiletype::PEM)
        .with_context(|| format!("Failed to load certificate from {:?}", cert_path))?;

    builder.set_private_key_file(key_path, SslFiletype::PEM)
        .with_context(|| format!("Failed to load private key from {:?}", key_path))?;

    builder.check_private_key().context("Private key does not match the certificate")?;

    info!("OpenSSL acceptor created successfully");
    Ok(builder.build())
}

// --- Request Parsing & Logging Logic  ---

fn parse_http_request(data: &[u8]) -> Result<RequestInfo> {
    let request_str = String::from_utf8_lossy(data);
    let mut request_info = RequestInfo::new();
    let lines: Vec<&str> = request_str.lines().collect();

    if lines.is_empty() { anyhow::bail!("Empty request"); }

    let parts: Vec<&str> = lines[0].split_whitespace().collect();
    if parts.len() >= 3 {
        request_info.method = parts[0].to_string();
        let path_query = parts[1];
        if let Some(pos) = path_query.find('?') {
            request_info.path = path_query[..pos].to_string();
            request_info.query = path_query[pos + 1..].to_string();
        } else {
            request_info.path = path_query.to_string();
        }
        request_info.version = parts[2].to_string();
    } else {
        request_info.method = "RAW".to_string();
        request_info.path = "/".to_string();
        request_info.version = "N/A".to_string();
        request_info.body = data.to_vec();
        return Ok(request_info);
    }

    let mut in_body = false;
    for line in lines.iter().skip(1) {
        if line.is_empty() { in_body = true; continue; }
        if !in_body {
            if let Some(pos) = line.find(':') {
                request_info.headers.insert(line[..pos].trim().to_string(), line[pos + 1..].trim().to_string());
            }
        } else {
            request_info.body.extend_from_slice(line.as_bytes());
            request_info.body.push(b'\n');
        }
    }
    if !request_info.body.is_empty() { request_info.body.pop(); }
    Ok(request_info)
}

fn create_request_log(
    addr: SocketAddr,
    protocol: &str,
    tls_version: Option<&str>,
    cipher_suite: Option<&str>,
    request_info: &RequestInfo,
    raw_request: &[u8],
    processing_time: u128,
) -> RequestLog {
    let body_preview = if request_info.body.len() > 100 {
        String::from_utf8_lossy(&request_info.body[..100]).to_string() + "..."
    } else {
        String::from_utf8_lossy(&request_info.body).to_string()
    };

    RequestLog {
        timestamp: Utc::now().to_rfc3339(),
        remote_addr: addr.to_string(),
        protocol: protocol.to_string(),
        tls_version: tls_version.map(|s| s.to_string()),
        cipher_suite: cipher_suite.map(|s| s.to_string()),
        method: request_info.method.clone(),
        path: request_info.path.clone(),
        query_string: request_info.query.clone(),
        http_version: request_info.version.clone(),
        headers: request_info.headers.clone(),
        body_size: request_info.body.len(),
        body_preview,
        raw_request: String::from_utf8_lossy(raw_request).to_string(),
        processing_time_ms: processing_time,
    }
}

fn log_request_details(log: &RequestLog) {
    info!("\n════════════════════════════════════════════════════════════════════════");
    info!("📥 NEW REQUEST (OpenSSL)");
    info!("════════════════════════════════════════════════════════════════════════");
    info!("Timestamp:       {}", log.timestamp);
    info!("Remote Address:  {}", log.remote_addr);
    info!("Protocol:        {}", log.protocol);
    if let Some(v) = &log.tls_version { info!("TLS Version:     {}", v); }
    if let Some(c) = &log.cipher_suite { info!("Cipher Suite:    {}", c); }
    info!("────────────────────────────────────────────────────────────────────────");
    info!("HTTP Request:    {} {} {}", log.method, log.path, log.http_version);
    info!("Processing Time: {} ms", log.processing_time_ms);
    info!("════════════════════════════════════════════════════════════════════════\n");
}


async fn route_request(request: &RequestInfo) -> String {
    match request.path.as_str() {
        // --- 1. Base64 encode ss.info ---
        "/hsg6ps3/ss.info" => {
            match fs::read("ss.info").await {
                Ok(contents) => {
                    let encoded = general_purpose::STANDARD.encode(contents);
                    format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: {}\r\n\r\n{}",
                        encoded.len(),
                        encoded
                    )
                }
                // Err(_) => "HTTP/1.1 404 NOT FOUND\r\n\r\nFile not found".to_string(),
                Err(_) => "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 10\r\n\r\n<ss>0</ss>".to_string(),
            }
        }

        // --- 2. Sanitized CGI execution ---
        path if path.starts_with("/cgi-bin/") => {
            let script_subpath = path.strip_prefix("/cgi-bin/").unwrap_or("");
            
            // SECURITY: Sanitize the path
            if !is_path_safe(script_subpath) {
                error!("Blocked malicious path attempt: {}", script_subpath);
                return "HTTP/1.1 403 FORBIDDEN\r\n\r\nForbidden".to_string();
            }

            let script_path = format!("./cgi-bin/{}", script_subpath);
            
            match Command::new(&script_path)
                .env("REQUEST_METHOD", &request.method)
                .env("QUERY_STRING", &request.query)
                .output()
                .await 
            {
                Ok(out) => {
                    let body = String::from_utf8_lossy(&out.stdout);
                    // Pass through script headers if they exist, otherwise wrap in 200 OK
                    if body.contains("Content-Type:") {
                        format!("HTTP/1.1 200 OK\r\n{}", body)
                    } else {
                        format!(
                            "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {}\r\n\r\n{}",
                            body.len(),
                            body
                        )
                    }
                }
                Err(e) => {
                    error!("CGI execution failed for {}: {:?}", script_path, e);
                    "HTTP/1.1 500 INTERNAL SERVER ERROR\r\n\r\nCGI Error".to_string()
                }
            }
        }

        _ => "HTTP/1.1 404 NOT FOUND\r\n\r\nNot Found".to_string(),
    }
}

/// Helper to ensure the path doesn't try to escape the cgi-bin directory
fn is_path_safe(path_str: &str) -> bool {
    let path = Path::new(path_str);
    
    // 1. Prevent empty paths or absolute paths (on the OS level)
    if path_str.is_empty() || path.is_absolute() {
        return false;
    }

    // 2. Iterate through components and block "ParentDir" (..) or "RootDir" (/)
    for component in path.components() {
        match component {
            Component::Normal(_) => continue,
            Component::CurDir => continue, // "." is okay
            _ => return false, // Blocks "..", "/", and Windows prefixes
        }
    }
    true
}

// --- Connection Handlers ---

async fn handle_tls_connection(
    mut stream: tokio_openssl::SslStream<TcpStream>,
    addr: SocketAddr,
) -> Result<()> {
    let start_time = std::time::Instant::now();
    let mut buffer = [0u8; 8192];

    let tls_version = Some(stream.ssl().version_str().to_string());
    let cipher_suite = stream.ssl().current_cipher().map(|c| c.name().to_string());

    let n = stream.read(&mut buffer).await?;
    if n == 0 { return Ok(()); }

    let raw_request = &buffer[..n];
    let request_info = parse_http_request(raw_request).unwrap_or_else(|_| {
        let mut info = RequestInfo::new();
        info.method = "UNKNOWN".to_string();
        info.body = raw_request.to_vec();
        info
    });

    // Use the new router
    let response = route_request(&request_info).await;

    let processing_time = start_time.elapsed().as_millis();
    let request_log = create_request_log(addr, "TLS", tls_version.as_deref(), cipher_suite.as_deref(), &request_info, raw_request, processing_time);
    log_request_details(&request_log);

    stream.write_all(response.as_bytes()).await?;
    stream.shutdown().await?;
    Ok(())
}

async fn handle_tcp_connection(mut stream: TcpStream, addr: SocketAddr) -> Result<()> {
    let start_time = std::time::Instant::now();
    let mut buffer = [0u8; 8192];
    let n = stream.read(&mut buffer).await?;
    if n == 0 { return Ok(()); }

    let raw_request = &buffer[..n];
    let request_info = parse_http_request(raw_request).unwrap_or_default(); // simplified
    
    // Use the new router
    let response = route_request(&request_info).await;

    let request_log = create_request_log(addr, "TCP", None, None, &request_info, raw_request, start_time.elapsed().as_millis());
    log_request_details(&request_log);

    stream.write_all(response.as_bytes()).await?;
    Ok(())
}

// --- Server Starters ---

async fn start_tls_server(acceptor: Arc<SslAcceptor>) -> Result<()> {
    let listener = TcpListener::bind("0.0.0.0:18443").await?;
    info!("🔐 OpenSSL server listening on 0.0.0.0:18443");

    loop {
        let (stream, addr) = listener.accept().await?;
        let acceptor = acceptor.clone();
        tokio::spawn(async move {
            let ssl = openssl::ssl::Ssl::new(acceptor.context()).unwrap();
            let mut tls_stream = tokio_openssl::SslStream::new(ssl, stream).unwrap();
            if let Err(e) = std::pin::Pin::new(&mut tls_stream).accept().await {
                error!("TLS handshake error: {:?}", e);
                return;
            }
            let _ = handle_tls_connection(tls_stream, addr).await;
        });
    }
}

async fn start_tcp_server() -> Result<()> {
    let listener = TcpListener::bind("0.0.0.0:18000").await?;
    info!("🔗 TCP server listening on 0.0.0.0:18000");
    loop {
        let (stream, addr) = listener.accept().await?;
        tokio::spawn(async move { let _ = handle_tcp_connection(stream, addr).await; });
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    println!("GravityGolf v0.01");
    tracing_subscriber::registry()
        .with(tracing_subscriber::fmt::layer().with_ansi(true))
        .init();

    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 {
        eprintln!("Usage: {} <cert> <key>", args[0]);
        std::process::exit(1);
    }

    let cert_path = PathBuf::from(&args[1]);
    let key_path = PathBuf::from(&args[2]);
    let acceptor = Arc::new(create_openssl_acceptor(&cert_path, &key_path)?);

    let _ = tokio::try_join!(start_tls_server(acceptor), start_tcp_server());
    Ok(())
}

impl Default for RequestInfo {
    fn default() -> Self { Self::new() }
}