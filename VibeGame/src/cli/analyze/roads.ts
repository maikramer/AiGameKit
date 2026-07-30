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
