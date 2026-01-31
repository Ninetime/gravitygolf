use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;
use std::fs;

use anyhow::{Context, Result};
use chrono::Utc;
use native_tls::TlsAcceptor;
use serde::{Deserialize, Serialize};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio_native_tls::TlsStream;
use tracing::{debug, error, info};
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;

// Request logging structures
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

// Create TLS acceptor with native-tls
fn create_tls_acceptor(cert_path: &PathBuf, key_path: &PathBuf) -> Result<TlsAcceptor> {
    // Read certificate and key files
    let cert_data = fs::read(cert_path)
        .with_context(|| format!("Failed to read certificate from {:?}", cert_path))?;
    let key_data = fs::read(key_path)
        .with_context(|| format!("Failed to read key from {:?}", key_path))?;

    // Create identity from cert and key
    let identity = native_tls::Identity::from_pkcs8(&cert_data, &key_data)
        .context("Failed to create TLS identity from certificate and key")?;

    // Create acceptor
    let acceptor = TlsAcceptor::new(identity)
        .context("Failed to create TLS acceptor")?;

    info!("TLS acceptor created successfully");
    Ok(acceptor)
}

// Parse HTTP request
fn parse_http_request(data: &[u8]) -> Result<RequestInfo> {
    let request_str = String::from_utf8_lossy(data);
    let mut request_info = RequestInfo::new();

    // Split request into lines
    let lines: Vec<&str> = request_str.lines().collect();

    if lines.is_empty() {
        anyhow::bail!("Empty request");
    }

    // Parse request line
    let request_line = lines[0];
    let parts: Vec<&str> = request_line.split_whitespace().collect();

    if parts.len() >= 3 {
        request_info.method = parts[0].to_string();

        // Split path and query
        let path_query = parts[1];
        if let Some(pos) = path_query.find('?') {
            request_info.path = path_query[..pos].to_string();
            request_info.query = path_query[pos + 1..].to_string();
        } else {
            request_info.path = path_query.to_string();
        }

        request_info.version = parts[2].to_string();
    } else {
        // Not a valid HTTP request, treat as raw data
        request_info.method = "RAW".to_string();
        request_info.path = "/".to_string();
        request_info.version = "N/A".to_string();
        request_info.body = data.to_vec();
        return Ok(request_info);
    }

    // Parse headers
    let mut in_body = false;
    let mut _body_start = 0;

    for (i, line) in lines.iter().enumerate().skip(1) {
        if line.is_empty() {
            // Empty line indicates end of headers
            in_body = true;
            _body_start = i + 1;
            continue;
        }

        if !in_body {
            if let Some(pos) = line.find(':') {
                let header_name = line[..pos].trim().to_string();
                let header_value = line[pos + 1..].trim().to_string();
                request_info.headers.insert(header_name, header_value);
            }
        } else {
            // Collect body lines
            request_info.body.extend_from_slice(line.as_bytes());
            request_info.body.push(b'\n');
        }
    }

    // Remove trailing newline from body
    if !request_info.body.is_empty() {
        request_info.body.pop();
    }

    Ok(request_info)
}

// Create request log
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

// Log request in a readable format
fn log_request_details(log: &RequestLog) {
    info!("\n════════════════════════════════════════════════════════════════════════");
    info!("📥 NEW REQUEST");
    info!("════════════════════════════════════════════════════════════════════════");
    info!("Timestamp:       {}", log.timestamp);
    info!("Remote Address:  {}", log.remote_addr);
    info!("Protocol:        {}", log.protocol);

    if let Some(tls_version) = &log.tls_version {
        info!("TLS Version:     {}", tls_version);
    }

    if let Some(cipher_suite) = &log.cipher_suite {
        info!("Cipher Suite:    {}", cipher_suite);
    }

    info!("────────────────────────────────────────────────────────────────────────");
    info!("HTTP Request:    {} {} {}", log.method, log.path, log.http_version);

    if !log.query_string.is_empty() {
        info!("Query String:    ?{}", log.query_string);
    }

    info!("──────────────────────────────────��─────────────────────────────────────");

    if !log.headers.is_empty() {
        info!("Headers:");
        for (key, value) in &log.headers {
            info!("  {}: {}", key, value);
        }
    }

    if log.body_size > 0 {
        info!("────────────────────────────────────────────────────────────────────────");
        info!("Body Size:       {} bytes", log.body_size);
        info!("Body Preview:    {}", log.body_preview);
    }

    info!("────────────────────────────────────────────────────────────────────────");
    info!("Processing Time: {} ms", log.processing_time_ms);
    info!("════════════════════════════════════════════════════════════════════════\n");
}

// Handle TLS connection with detailed logging
async fn handle_tls_connection(
    mut tls_stream: TlsStream<TcpStream>,
    addr: SocketAddr,
) -> Result<()> {
    let start_time = std::time::Instant::now();
    let mut buffer = [0u8; 8192];

    // Note: native-tls doesn't expose TLS version and cipher suite details
    // This is a limitation of the native-tls library on Windows (uses Schannel)
    let tls_version = Some("TLS 1.2+ (Windows Schannel)".to_string());
    let cipher_suite: Option<String> = None;

    let n = tls_stream
        .read(&mut buffer)
        .await
        .with_context(|| "Failed to read from TLS connection")?;

    if n == 0 {
        info!("TLS connection closed by client: {}", addr);
        return Ok(());
    }

    info!("TLS Handshake successful from {}", addr);

    let raw_request = &buffer[..n];

    // Parse HTTP request
    let request_info = parse_http_request(raw_request).unwrap_or_else(|_| {
        let mut info = RequestInfo::new();
        info.method = "UNKNOWN".to_string();
        info.path = "/".to_string();
        info.version = "N/A".to_string();
        info.body = raw_request.to_vec();
        info
    });

    // Create and log request
    let processing_time = start_time.elapsed().as_millis();
    let request_log = create_request_log(
        addr,
        "TLS",
        tls_version.as_deref(),
        cipher_suite.as_deref(),
        &request_info,
        raw_request,
        processing_time,
    );

    // Log request details
    log_request_details(&request_log);

    // Also output as JSON for machine-readable logging
    let json_log = serde_json::to_string_pretty(&request_log)
        .unwrap_or_else(|_| "Failed to serialize log".to_string());
    debug!("Request JSON:\n{}", json_log);

    // Prepare response
    let response = if request_info.method == "RAW" {
        // Echo back raw data
        raw_request.to_vec()
    } else {
        // Create HTTP response
        let response_body = format!(
            "Request received successfully!\n\
            \n\
            📋 Request Details:\n\
            - Method: {}\n\
            - Path: {}\n\
            - Query: {}\n\
            - Protocol: {}\n\
            - TLS Version: {}\n\
            - Headers: {}\n\
            - Body Size: {} bytes\n\
            - From: {}\n\
            - Processed in: {} ms\n\
            \n\
            This server supports both TLS (port 18443) and plain TCP (port 18000)\n\
            ",
            request_info.method,
            request_info.path,
            if request_info.query.is_empty() {
                "None".to_string()
            } else {
                request_info.query.clone()
            },
            if tls_version.is_some() { "HTTPS/TLS" } else { "HTTP" },
            tls_version.as_deref().unwrap_or("N/A"),
            request_info.headers.len(),
            request_info.body.len(),
            addr,
            processing_time
        );

        format!(
            "HTTP/1.1 200 OK\r\n\
            Content-Type: text/plain\r\n\
            Content-Length: {}\r\n\
            Server: Rust TLS/TCP Server\r\n\
            Connection: close\r\n\
            \r\n\
            {}",
            response_body.len(),
            response_body
        )
        .as_bytes()
        .to_vec()
    };

    // Send response
    tls_stream
        .write_all(&response)
        .await
        .with_context(|| "Failed to write to TLS connection")?;
    tls_stream
        .flush()
        .await
        .with_context(|| "Failed to flush TLS connection")?;

    info!("Response sent to {} in {} ms", addr, processing_time);

    Ok(())
}

// Handle plain TCP connection with detailed logging
async fn handle_tcp_connection(mut stream: TcpStream, addr: SocketAddr) -> Result<()> {
    let start_time = std::time::Instant::now();
    let mut buffer = [0u8; 8192];

    let n = stream
        .read(&mut buffer)
        .await
        .with_context(|| "Failed to read from TCP connection")?;

    if n == 0 {
        info!("TCP connection closed by client: {}", addr);
        return Ok(());
    }

    let raw_request = &buffer[..n];

    // Parse HTTP request
    let request_info = parse_http_request(raw_request).unwrap_or_else(|_| {
        let mut info = RequestInfo::new();
        info.method = "UNKNOWN".to_string();
        info.path = "/".to_string();
        info.version = "N/A".to_string();
        info.body = raw_request.to_vec();
        info
    });

    // Create and log request
    let processing_time = start_time.elapsed().as_millis();
    let request_log = create_request_log(
        addr,
        "TCP",
        None,
        None,
        &request_info,
        raw_request,
        processing_time,
    );

    // Log request details
    log_request_details(&request_log);

    // Also output as JSON for machine-readable logging
    let json_log = serde_json::to_string_pretty(&request_log)
        .unwrap_or_else(|_| "Failed to serialize log".to_string());
    debug!("Request JSON:\n{}", json_log);

    // Prepare response
    let response = if request_info.method == "RAW" {
        // Echo back raw data
        raw_request.to_vec()
    } else {
        // Create HTTP response
        let response_body = format!(
            "Request received successfully!\n\
            \n\
            📋 Request Details:\n\
            - Method: {}\n\
            - Path: {}\n\
            - Query: {}\n\
            - Protocol: HTTP (Plain TCP)\n\
            - Headers: {}\n\
            - Body Size: {} bytes\n\
            - From: {}\n\
            - Processed in: {} ms\n\
            \n\
            This server supports both TLS (port 18443) and plain TCP (port 18000)\n\
            ",
            request_info.method,
            request_info.path,
            if request_info.query.is_empty() {
                "None"
            } else {
                &request_info.query
            },
            request_info.headers.len(),
            request_info.body.len(),
            addr,
            processing_time
        );

        format!(
            "HTTP/1.1 200 OK\r\n\
            Content-Type: text/plain\r\n\
            Content-Length: {}\r\n\
            Server: Rust TLS/TCP Server\r\n\
            Connection: close\r\n\
            \r\n\
            {}",
            response_body.len(),
            response_body
        )
        .as_bytes()
        .to_vec()
    };

    // Send response
    stream
        .write_all(&response)
        .await
        .with_context(|| "Failed to write to TCP connection")?;
    stream
        .flush()
        .await
        .with_context(|| "Failed to flush TCP connection")?;

    info!("Response sent to {} in {} ms", addr, processing_time);

    Ok(())
}

// Start TLS server on port 18443
async fn start_tls_server(tls_acceptor: Arc<TlsAcceptor>) -> Result<()> {
    let listener = TcpListener::bind("0.0.0.0:18443")
        .await
        .with_context(|| "Failed to bind TLS server to port 18443")?;

    info!("🔐 TLS server listening on 0.0.0.0:18443");

    loop {
        let (stream, addr) = listener
            .accept()
            .await
            .with_context(|| "Failed to accept TLS connection")?;

        let acceptor = tls_acceptor.clone();

        tokio::spawn(async move {
            let tokio_acceptor = tokio_native_tls::TlsAcceptor::from(acceptor.as_ref().clone());
            match tokio_acceptor.accept(stream).await {
                Ok(tls_stream) => {
                    if let Err(e) = handle_tls_connection(tls_stream, addr).await {
                        error!("Error handling TLS connection from {}: {:?}", addr, e);
                    }
                }
                Err(e) => {
                    error!("Failed to establish TLS connection from {}: {:?}", addr, e);
                }
            }
        });
    }
}

// Start TCP server on port 18000
async fn start_tcp_server() -> Result<()> {
    let listener = TcpListener::bind("0.0.0.0:18000")
        .await
        .with_context(|| "Failed to bind TCP server to port 18000")?;

    info!("🔗 TCP server listening on 0.0.0.0:18000");

    loop {
        let (stream, addr) = listener
            .accept()
            .await
            .with_context(|| "Failed to accept TCP connection")?;

        tokio::spawn(async move {
            if let Err(e) = handle_tcp_connection(stream, addr).await {
                error!("Error handling TCP connection from {}: {:?}", addr, e);
            }
        });
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    // Create/open a log file
    let log_file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open("gravitygolf.log")
        .context("Failed to create log file")?;

    // Initialize logging with both console and file output
    let file_layer = tracing_subscriber::fmt::layer()
        .with_target(true)
        .with_timer(tracing_subscriber::fmt::time::UtcTime::rfc_3339())
        .with_level(true)
        .with_ansi(false)  // No colors in file
        .with_writer(Arc::new(log_file));

    let console_layer = tracing_subscriber::fmt::layer()
        .with_target(true)
        .with_timer(tracing_subscriber::fmt::time::UtcTime::rfc_3339())
        .with_level(true)
        .with_ansi(true);  // Colors in console

    tracing_subscriber::registry()
        .with(console_layer)
        .with(file_layer)
        .init();

    info!("🚀 Starting gravitygolf server...");

    // Check for command line arguments
    let args: Vec<String> = std::env::args().collect();

    if args.len() < 3 {
        eprintln!("Usage: {} <certificate_path> <private_key_path>", args[0]);
        eprintln!("Example: {} cert.pem key.pem", args[0]);
        std::process::exit(1);
    }

    let cert_path = PathBuf::from(&args[1]);
    let key_path = PathBuf::from(&args[2]);

    // Create TLS acceptor
    let tls_acceptor = Arc::new(create_tls_acceptor(&cert_path, &key_path)?);

    // Display server information
    info!("════════════════════════════════════════════════════════════════════════");
    info!("Server Configuration:");
    info!("  • TLS Port:    18443");
    info!("  • TCP Port:    18000");
    info!("  • Certificate: {:?}", cert_path);
    info!("  • Private Key: {:?}", key_path);
    info!("  • TLS Library: native-tls (Windows Schannel)");
    info!("  • Log File:    gravitygolf.log");
    info!("════════════════════════════════════════════════════════════════════════");

    // Start both servers concurrently
    let tls_server = start_tls_server(tls_acceptor);
    let tcp_server = start_tcp_server();

    // Run both servers
    tokio::try_join!(tls_server, tcp_server)?;

    Ok(())
}