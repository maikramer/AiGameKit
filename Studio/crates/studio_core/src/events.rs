//! Eventos do pipeline e parsing do JSONL.

use std::{collections::BTreeMap, time::Duration};

use serde::Deserialize;

/// Linha JSONL emitida por `pipeline._stage` e pelas tools.
#[derive(Debug, Clone, Deserialize, Default)]
pub struct ProgressEvent {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub tool: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub phase: Option<String>,
    #[serde(default)]
    pub percent: Option<f32>,
    #[serde(default)]
    pub output: Option<String>,
    #[serde(default)]
    pub seconds: Option<f32>,
    #[serde(default)]
    pub faces: Option<u64>,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub meta: serde_json::Value,
}

impl ProgressEvent {
    pub fn is_start(&self) -> bool {
        self.status == "progress" && self.percent.unwrap_or(-1.0) <= 0.0
    }

    pub fn is_finish(&self) -> bool {
        (self.status == "progress" && self.percent.unwrap_or(-1.0) >= 100.0)
            || matches!(self.status.as_str(), "ok" | "skipped" | "error")
    }

    pub fn stage_name(&self) -> &str {
        if self.tool.is_empty() {
            &self.id
        } else {
            &self.tool
        }
    }
}

pub fn parse_progress(line: &str) -> Option<ProgressEvent> {
    let trimmed = line.trim();
    if !trimmed.starts_with('{') || !trimmed.ends_with('}') {
        return None;
    }
    let value: serde_json::Value = serde_json::from_str(trimmed).ok()?;
    if !value.is_object() || value.get("id").is_none() || value.get("status").is_none() {
        return None;
    }
    serde_json::from_value(value).ok()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stream {
    Stdout,
    Stderr,
}

#[derive(Debug, Clone)]
pub enum PipelineEvent {
    Started {
        command: String,
    },
    Line {
        stream: Stream,
        line: String,
    },
    Progress(ProgressEvent),
    Finished {
        code: Option<i32>,
        success: bool,
        cancelled: bool,
        duration: Duration,
    },
    SpawnError(String),
}

/// Registo do `--log` JSONL do resume/batch.
#[derive(Debug, Clone, Deserialize, Default)]
pub struct RunLogRecord {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub error: Option<String>,
    #[serde(default)]
    pub stages: Vec<StageRecord>,
    #[serde(default)]
    pub timing: BTreeMap<String, f64>,
    #[serde(default)]
    pub total_elapsed_s: Option<f64>,
    #[serde(default)]
    pub mesh_path: Option<String>,
    #[serde(default)]
    pub lod0_path: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct StageRecord {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub ok: bool,
    #[serde(default)]
    pub elapsed_s: Option<f64>,
    #[serde(default)]
    pub error: Option<String>,
}

pub fn parse_run_log(text: &str) -> Vec<RunLogRecord> {
    text.lines()
        .filter_map(|line| {
            let line = line.trim();
            if !line.starts_with('{') {
                return None;
            }
            serde_json::from_str(line).ok()
        })
        .collect()
}

#[derive(Debug, Clone)]
pub struct StageFailure {
    pub asset: String,
    pub stage: String,
    pub error: String,
}

pub fn stage_failures(records: &[RunLogRecord]) -> Vec<StageFailure> {
    let mut out = Vec::new();
    for record in records {
        for stage in &record.stages {
            if !stage.ok {
                if let Some(error) = &stage.error {
                    out.push(StageFailure {
                        asset: record.id.clone(),
                        stage: stage.name.clone(),
                        error: error.clone(),
                    });
                }
            }
        }
        if let Some(error) = &record.error {
            out.push(StageFailure {
                asset: record.id.clone(),
                stage: String::new(),
                error: error.clone(),
            });
        }
    }
    out
}

/// Timings por etapa (última ocorrência ganha).
pub fn stage_timings(records: &[RunLogRecord]) -> BTreeMap<String, f64> {
    let mut out = BTreeMap::new();
    for record in records {
        for (name, seconds) in &record.timing {
            out.insert(name.clone(), *seconds);
        }
        for stage in &record.stages {
            if let Some(seconds) = stage.elapsed_s {
                out.insert(stage.name.clone(), seconds);
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_stage_progress_lines() {
        let event = parse_progress(
            r#"{"id":"hero","tool":"topology_fix","status":"progress","phase":"run","percent":100.0,"meta":{"seconds":2.34}}"#,
        )
        .expect("event");
        assert_eq!(event.id, "hero");
        assert_eq!(event.stage_name(), "topology_fix");
        assert!(event.is_finish());
        assert_eq!(event.percent, Some(100.0));
    }

    #[test]
    fn ignores_non_progress_json_and_text() {
        assert!(parse_progress("hello").is_none());
        assert!(parse_progress(r#"{"foo":1}"#).is_none());
        assert!(parse_progress(r#"{"event":"pipeline_stage","stage":"lod"}"#).is_none());
    }

    #[test]
    fn parses_run_log_and_failures() {
        let text = r#"{"id":"hero","status":"error","stages":[{"name":"lod","ok":false,"elapsed_s":1.5,"error":"boom"}],"timing":{"paint":12.0},"error":"final"}"#;
        let records = parse_run_log(text);
        assert_eq!(records.len(), 1);
        let failures = stage_failures(&records);
        assert_eq!(failures.len(), 2);
        assert_eq!(stage_timings(&records).get("paint"), Some(&12.0));
    }
}
