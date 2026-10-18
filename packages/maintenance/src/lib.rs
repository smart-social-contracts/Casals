//! casals-maintenance: the page a baton installs into a stand's frontend canisters
//! while a release upgrade runs. Every path answers a certified 503 with the
//! maintenance page, except the files the baton copied from the frontend (such as
//! `/.well-known/ic-domains`), which keep custom domains working.

use std::cell::RefCell;

use candid::{CandidType, Deserialize};
use ic_asset_certification::{Asset, AssetConfig, AssetFallbackConfig, AssetRouter};
use ic_http_certification::{HttpRequest, HttpResponse, StatusCode};

const PAGE_PATH: &str = "/__casals/maintenance.html";
const LOGO_PATH: &str = "/__casals/logo.png";
const LOGO: &[u8] = include_bytes!("logo.png");
const RELOAD_SECONDS: u64 = 30;

#[derive(CandidType, Deserialize, Clone, Default)]
pub struct Status {
    pub message: String,
    pub title: Option<String>,
    pub release: Option<String>,
    /// Expected end of the window, in nanoseconds since the epoch.
    pub until_ns: Option<u64>,
}

#[derive(CandidType, Deserialize, Clone)]
pub struct File {
    pub path: String,
    pub content_type: String,
    pub body: Vec<u8>,
}

#[derive(CandidType, Deserialize, Clone, Default)]
pub struct InitArg {
    pub status: Status,
    pub files: Vec<File>,
}

thread_local! {
    static STATE: RefCell<InitArg> = RefCell::new(InitArg::default());
    static ROUTER: RefCell<AssetRouter<'static>> = RefCell::new(AssetRouter::default());
}

fn escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}

fn retry_after(status: &Status, now_ns: u64) -> u64 {
    match status.until_ns {
        Some(until) if until > now_ns => ((until - now_ns) / 1_000_000_000).clamp(RELOAD_SECONDS, 3600),
        _ => RELOAD_SECONDS * 4,
    }
}

pub fn render(status: &Status) -> String {
    let title = escape(status.title.as_deref().unwrap_or("Down for maintenance"));
    let message = if status.message.trim().is_empty() {
        "We are upgrading this service. It will be back shortly.".to_string()
    } else {
        escape(&status.message)
    };
    let release = status
        .release
        .as_deref()
        .map(|r| format!("<p class=\"release\">Upgrading to {}</p>", escape(r)))
        .unwrap_or_default();
    let until = status
        .until_ns
        .map(|ns| {
            format!(
                "<p class=\"until\">Expected back around <time data-ms=\"{}\">{} UTC</time></p>",
                ns / 1_000_000,
                ns / 1_000_000_000
            )
        })
        .unwrap_or_default();
    format!(
        r#"<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{RELOAD_SECONDS}">
<meta name="robots" content="noindex">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin: 0; min-height: 100vh; display: flex; flex-direction: column; align-items: center;
         justify-content: center; font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
         background: #fafafa; color: #1f2328; }}
  main {{ max-width: 34rem; padding: 2rem; text-align: center; }}
  h1 {{ font-size: 1.6rem; font-weight: 600; margin: 0 0 .75rem; }}
  p {{ margin: .5rem 0; }}
  .release, .until {{ color: #59636e; font-size: .9rem; }}
  .dot {{ display: inline-block; width: .6rem; height: .6rem; border-radius: 50%; background: #d4a72c;
         margin-right: .5rem; animation: pulse 1.6s ease-in-out infinite; }}
  @keyframes pulse {{ 50% {{ opacity: .3; }} }}
  footer {{ position: fixed; bottom: 1.25rem; display: flex; align-items: center; gap: .4rem;
           font-size: .8rem; color: #8c959f; }}
  footer img {{ width: 20px; height: 20px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #0d1117; color: #e6edf3; }}
    .release, .until {{ color: #9198a1; }}
    footer img {{ filter: invert(1); opacity: .8; }}
  }}
</style>
</head>
<body>
<main>
  <h1><span class="dot"></span>{title}</h1>
  <p>{message}</p>
  {until}
  {release}
</main>
<footer><img src="{LOGO_PATH}" alt="">Powered by Casals</footer>
<script>
  for (const t of document.querySelectorAll("time[data-ms]")) {{
    t.textContent = new Date(Number(t.dataset.ms)).toLocaleString();
  }}
</script>
</body>
</html>
"#
    )
}

fn certify(state: &InitArg) {
    let now = ic_cdk::api::time();
    let html = render(&state.status);
    let no_store = ("cache-control".to_string(), "no-store".to_string());
    let mut assets = vec![
        Asset::new(PAGE_PATH, html.into_bytes()),
        Asset::new(LOGO_PATH, LOGO),
    ];
    let mut configs = vec![
        AssetConfig::File {
            path: PAGE_PATH.to_string(),
            content_type: Some("text/html; charset=utf-8".to_string()),
            headers: vec![
                no_store.clone(),
                ("retry-after".to_string(), retry_after(&state.status, now).to_string()),
                ("x-content-type-options".to_string(), "nosniff".to_string()),
            ],
            fallback_for: vec![AssetFallbackConfig {
                scope: "/".to_string(),
                status_code: Some(StatusCode::SERVICE_UNAVAILABLE),
            }],
            aliased_by: vec![],
            encodings: vec![],
        },
        AssetConfig::File {
            path: LOGO_PATH.to_string(),
            content_type: Some("image/png".to_string()),
            headers: vec![("cache-control".to_string(), "public, max-age=3600".to_string())],
            fallback_for: vec![],
            aliased_by: vec![],
            encodings: vec![],
        },
    ];
    for f in &state.files {
        if f.path == PAGE_PATH || f.path == LOGO_PATH || !f.path.starts_with('/') {
            continue;
        }
        assets.push(Asset::new(f.path.clone(), f.body.clone()));
        configs.push(AssetConfig::File {
            path: f.path.clone(),
            content_type: Some(f.content_type.clone()),
            headers: vec![no_store.clone()],
            fallback_for: vec![],
            aliased_by: vec![],
            encodings: vec![],
        });
    }
    ROUTER.with_borrow_mut(|router| {
        *router = AssetRouter::default();
        router
            .certify_assets(assets, configs)
            .unwrap_or_else(|e| ic_cdk::trap(format!("certify: {e}")));
        ic_cdk::api::certified_data_set(router.root_hash());
    });
}

fn load(arg: Option<InitArg>) {
    let state = arg.unwrap_or_default();
    certify(&state);
    STATE.with_borrow_mut(|s| *s = state);
}

#[ic_cdk::init]
fn init(arg: Option<InitArg>) {
    load(arg);
}

#[ic_cdk::post_upgrade]
fn post_upgrade(arg: Option<InitArg>) {
    load(arg);
}

#[ic_cdk::update]
fn set_status(status: Status) {
    if !ic_cdk::api::is_controller(&ic_cdk::api::msg_caller()) {
        ic_cdk::trap("only a controller may set the maintenance status");
    }
    let state = STATE.with_borrow_mut(|s| {
        s.status = status;
        s.clone()
    });
    certify(&state);
}

#[ic_cdk::query]
fn status() -> Status {
    STATE.with_borrow(|s| s.status.clone())
}

#[ic_cdk::query]
fn http_request(req: HttpRequest<'static>) -> HttpResponse<'static> {
    let cert = ic_cdk::api::data_certificate().unwrap_or_default();
    ROUTER.with_borrow(|router| {
        router.serve_asset(&cert, &req).unwrap_or_else(|_| {
            HttpResponse::builder()
                .with_status_code(StatusCode::SERVICE_UNAVAILABLE)
                .with_body(b"Down for maintenance".to_vec())
                .build()
        })
    })
}

ic_cdk::export_candid!();

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn page_escapes_and_mentions_release() {
        let html = render(&Status {
            message: "<b>back</b> soon".into(),
            title: None,
            release: Some("realm@0.7.0".into()),
            until_ns: Some(1_800_000_000_000_000_000),
        });
        assert!(html.contains("&lt;b&gt;back&lt;/b&gt; soon"));
        assert!(html.contains("Upgrading to realm@0.7.0"));
        assert!(html.contains("data-ms=\"1800000000000\""));
        assert!(html.contains("Powered by Casals"));
        assert!(!html.contains("<b>back"));
    }

    #[test]
    fn retry_after_tracks_the_window() {
        let s = Status { until_ns: Some(600_000_000_000), ..Default::default() };
        assert_eq!(retry_after(&s, 0), 600);
        assert_eq!(retry_after(&s, 590_000_000_000), RELOAD_SECONDS);
        assert_eq!(retry_after(&Status::default(), 0), RELOAD_SECONDS * 4);
    }
}
