use std::collections::HashMap;
use std::fs::File;
use std::io::BufReader;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use anyhow::{Context, Result};
use chrono::Utc;
use rustls::{
    cipher_suite::*, ServerConfig, version,
};
use rustls_pemfile;
use serde::{Deserialize, Serialize};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio_rustls::TlsAcceptor;
use tracing::{debug, error, info, warn};

// Request logging structures
#[derive(Debug, Serialize, Deserialize, Clone)]
struct RequestLog {
    timestamp: String,
    remote_addr: String,
    protocol: String,
    tls_version: Option<String>,
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

// Function to load certificates from file
fn load_certificates(cert_path: &PathBuf) -> Result<Vec<rustls::Certificate>> {
    let cert_file = File::open(cert_path)
        .with_context(|| format!("Failed to open certificate file: {:?}", cert_path))?;
    let mut reader = BufReader::new(cert_file);

    let certs: Vec<rustls::Certificate> = rustls_pemfile::certs(&mut reader)
        .collect::<Result<Vec<_>, _>>()
        .with_context(|| "Failed to parse certificates")?
        .into_iter()
        .map(|cert| rustls::Certificate(cert.to_vec()))
        .collect();

    if certs.is_empty() {
        anyhow::bail!("No certificates found in file");
    }

    info!("Loaded {} certificate(s)", certs.len());
    Ok(certs)
}

fn load_private_key(key_path: &PathBuf) -> Result<rustls::PrivateKey> {
    let key_file = File::open(key_path)
        .with_context(|| format!("Failed to open key file: {:?}", key_path))?;
    let mut reader = BufReader::new(key_file);

    // Try PKCS8 first
    let pkcs8_keys: Vec<rustls_pki_types::PrivatePkcs8KeyDer<'static>> = rustls_pemfile::pkcs8_private_keys(&mut reader)
        .collect::<Result<Vec<_>, _>>()
        .with_context(|| "Failed to parse PKCS8 private key")?;

    if !pkcs8_keys.is_empty() {
        if pkcs8_keys.len() > 1 {
            warn!("Multiple private keys found, using the first one");
        }

        let key_bytes = pkcs8_keys[0].secret_pkcs8_der();
        info!("Private key loaded successfully (PKCS8) - {} bytes", key_bytes.len());
        return Ok(rustls::PrivateKey(key_bytes.to_vec()));
    }

    // Try RSA if PKCS8 is not found
    let key_file = File::open(key_path)
        .with_context(|| format!("Failed to reopen key file: {:?}", key_path))?;
    let mut reader = BufReader::new(key_file);

    let rsa_keys: Vec<rustls_pki_types::PrivatePkcs1KeyDer<'static>> = rustls_pemfile::rsa_private_keys(&mut reader)
        .collect::<Result<Vec<_>, _>>()
        .with_context(|| "Failed to parse RSA private key")?;

    if !rsa_keys.is_empty() {
        if rsa_keys.len() > 1 {
            warn!("Multiple private keys found, using the first one");
        }

        let key_bytes = rsa_keys[0].secret_pkcs1_der();
        info!("Private key loaded successfully (PKCS1/RSA) - {} bytes", key_bytes.len());
        return Ok(rustls::PrivateKey(key_bytes.to_vec()));
    }

    anyhow::bail!("No private keys found in file - tried PKCS8 and PKCS1 formats");
}

// Create TLS server configuration
fn create_tls_config(cert_path: &PathBuf, key_path: &PathBuf) -> Result<ServerConfig> {
    let certs = load_certificates(cert_path)?;
    let key = load_private_key(key_path)?;

    // Try to include as many cipher suites as possible for PS3 compatibility
    let cipher_suites: Vec<&'static rustls::SupportedCipherSuite> = vec![
        // TLS 1.3 cipher suites
        rustls::cipher_suite::TLS13_AES_256_GCM_SHA384,
        rustls::cipher_suite::TLS13_CHACHA20_POLY1305_SHA256,
        rustls::cipher_suite::TLS13_AES_128_GCM_SHA256,
        
        // TLS 1.2 ECDHE cipher suites
        rustls::cipher_suite::TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,
        rustls::cipher_suite::TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,
        rustls::cipher_suite::TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256,
        
        // TLS 1.2 RSA cipher suites (older, for legacy support)
        rustls::cipher_suite::TLS_RSA_WITH_AES_256_GCM_SHA384,
        rustls::cipher_suite::TLS_RSA_WITH_AES_128_GCM_SHA256,
        
        // TLS 1.2 ECDHE with SHA (older)
        rustls::cipher_suite::TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA,
        rustls::cipher_suite::TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA,
        
        // TLS 1.2 RSA with CBC (very old, PS3 era)
        rustls::cipher_suite::TLS_RSA_WITH_AES_256_CBC_SHA,
        rustls::cipher_suite::TLS_RSA_WITH_AES_128_CBC_SHA,
    ];

    let config = ServerConfig::builder()
        .with_cipher_suites(&cipher_suites)
        .with_safe_default_kx_groups()
        .with_protocol_versions(&[&rustls::version::TLS13, &rustls::version::TLS12])
        .with_context(|| "Failed to set protocol versions")?
        .with_no_client_auth()
        .with_single_cert(certs, key)
        .with_context(|| "Failed to load certificate or key")?;

    info!("TLS configuration created successfully");
    info!("Supported TLS versions: TLSv1.2, TLSv1.3");
    info!("Supported cipher suites: {} configured", cipher_suites.len());

    Ok(config)
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

    info!("────────────────────────────────────────────────────────────────────────");
    info!("HTTP Request:    {} {} {}", log.method, log.path, log.http_version);

    if !log.query_string.is_empty() {
        info!("Query String:    ?{}", log.query_string);
    }

    info!("────────────────────────────────────────────────────────────────────────");

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
    mut tls_stream: tokio_rustls::server::TlsStream<TcpStream>,
    addr: SocketAddr,
) -> Result<()> {
    let start_time = std::time::Instant::now();
    let mut buffer = [0u8; 8192];

    // Get TLS version
    let tls_version = tls_stream
        .get_ref()
        .1
        .protocol_version()
        .map(|v| format!("{:?}", v));

    let n = tls_stream
        .read(&mut buffer)
        .await
        .with_context(|| "Failed to read from TLS connection")?;

    if n == 0 {
        info!("TLS connection closed by client: {}", addr);
        return Ok(());
    }
    
    // In your handle_tls_connection function, after getting tls_version:
    let cipher_suite = tls_stream
        .get_ref()
        .1
        .negotiated_cipher_suite()
        .map(|cs| format!("{:?}", cs));

    info!("Negotiated cipher suite: {:?}", cipher_suite);

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
            if tls_version.is_some() { "HTTPS/TLS" } else { "HTTP" },
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
async fn start_tls_server(tls_acceptor: TlsAcceptor) -> Result<()> {
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
            match acceptor.accept(stream).await {
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
    // Initialize logging with colors and timestamps
    tracing_subscriber::fmt()
        .with_target(true)
        .with_timer(tracing_subscriber::fmt::time::UtcTime::rfc_3339())
        .with_level(true)
        .with_ansi(true)
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

    // Create TLS configuration
    let tls_config = create_tls_config(&cert_path, &key_path)?;
    let tls_acceptor = TlsAcceptor::from(Arc::new(tls_config));

    // Display server information
    info!("════════════════════════════════════════════════════════════════════════");
    info!("Server Configuration:");
    info!("  • TLS Port:    18443");
    info!("  • TCP Port:    18000");
    info!("  • Certificate: {:?}", cert_path);
    info!("  • Private Key: {:?}", key_path);
    info!("  • WARNING: Using dangerous certificate verification (1024-bit support)");
    info!("════════════════════════════════════════════════════════════════════════");

    // Start both servers concurrently
    let tls_server = start_tls_server(tls_acceptor);
    let tcp_server = start_tcp_server();

    // Run both servers
    tokio::try_join!(tls_server, tcp_server)?;

    Ok(())
}