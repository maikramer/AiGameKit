use std::process::ExitCode;

#[tokio::main]
async fn main() -> ExitCode {
    materialize_cli::app::main_entry().await
}
