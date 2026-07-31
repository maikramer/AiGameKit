import type { ParsedElement } from '../../core';
import {
  buildRoadNetworkGraph,
  parseRoadNetworkElement,
  pathBetweenWays,
  wayDegrees,
} from '../../plugins/road/network';
import type { AnalyzeIssue } from './types';

function walk(el: ParsedElement, visit: (e: ParsedElement) => void): void {
  visit(el);
  for (const c of el.children) walk(c, visit);
}

/**
 * Static checks on `<RoadNetwork>` graphs after Include expand:
 * orphan Ways, unknown Segment refs (parse throws), disconnected components,
 * missing path between cardinal tips when both exist.
 */
export function checkRoadNetworks(root: ParsedElement): AnalyzeIssue[] {
  const issues: AnalyzeIssue[] = [];
  walk(root, (el) => {
    if (el.tagName.toLowerCase() !== 'roadnetwork') return;
    let def;
    try {
      def = parseRoadNetworkElement(el);
    } catch (e) {
      issues.push({
        severity: 'error',
        code: 'road',
        message: e instanceof Error ? e.message : String(e),
      });
      return;
    }
    const deg = wayDegrees(def);
    for (const [id, d] of deg) {
      if (d === 0) {
        issues.push({
          severity: 'warn',
          code: 'road',
          message: `[RoadNetwork] orphan Way id="${id}" (no Segments)`,
        });
      }
    }
    if (def.segments.length === 0) {
      issues.push({
        severity: 'warn',
        code: 'road',
        message: '[RoadNetwork] has Ways but zero Segments',
      });
      return;
    }
    const graph = buildRoadNetworkGraph(def);
    for (const seg of def.segments) {
      const isBridge = seg.profile === 'bridge' || !!seg.bridgeUrl;
      if (isBridge && !seg.bridgeUrl) {
        issues.push({
          severity: 'error',
          code: 'road',
          message: `[RoadNetwork] bridge Segment ${seg.a}→${seg.b} missing bridge-url`,
        });
      }
      if (isBridge && seg.bridgeUrl) {
        const wa = def.ways.get(seg.a);
        const wb = def.ways.get(seg.b);
        if (wa && wb) {
          const path = [wa.x, wa.z, ...seg.via, wb.x, wb.z];
          const native = seg.bridgeNativeSpan ?? 18;
          let span = 0;
          for (let i = 2; i < path.length; i += 2) {
            span += Math.hypot(
              path[i]! - path[i - 2]!,
              path[i + 1]! - path[i - 1]!
            );
          }
          const ratio = native > 0 ? span / native : 1;
          if (ratio < 0.45 || ratio > 2.75) {
            issues.push({
              severity: 'warn',
              code: 'road',
              message: `[RoadNetwork] bridge ${seg.a}→${seg.b} span≈${span.toFixed(1)}m vs native ${native}m (ratio ${ratio.toFixed(2)}; stretch looks odd)`,
            });
          }
        }
      }
    }
    // Soft connectivity hint: plaza ↔ farthest tips if named.
    const tips = ['desert_end', 'n_end', 's_end', 'w_end'];
    if (graph.ways.has('plaza')) {
      for (const tip of tips) {
        if (!graph.ways.has(tip)) continue;
        const path = pathBetweenWays(graph, 'plaza', tip);
        if (!path) {
          issues.push({
            severity: 'info',
            code: 'road',
            message: `[RoadNetwork] plaza ↛ ${tip} (gap/bridge expected or missing Segment)`,
          });
        }
      }
    }
  });
  return issues;
}
