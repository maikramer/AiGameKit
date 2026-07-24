import type { AnalyzeIssue, AnalyzeResult } from './types';

export function formatTextReport(result: AnalyzeResult): string {
  const lines: string[] = [];
  lines.push(
    `[analyze] ${result.entry}  (public=${result.publicDir})  ` +
      `footprints=${result.footprintCount}  ` +
      `errors=${result.errorCount}  warns=${result.warnCount}`
  );

  const order: AnalyzeIssue['severity'][] = ['error', 'warn', 'info'];
  for (const sev of order) {
    for (const issue of result.issues.filter((i) => i.severity === sev)) {
      lines.push(issue.message);
      if (issue.detail) lines.push(...issue.detail);
    }
  }

  if (result.errorCount === 0 && result.warnCount === 0) {
    lines.push('[analyze] OK — no issues');
  }
  return lines.join('\n');
}

export function formatJsonReport(result: AnalyzeResult): string {
  return JSON.stringify(result, null, 2);
}

export function shouldFail(
  result: AnalyzeResult,
  failOn: 'error' | 'warn'
): boolean {
  if (result.errorCount > 0) return true;
  if (failOn === 'warn' && result.warnCount > 0) return true;
  return false;
}
