//! 2D path utilities for rivers and roads — ported from the VibeGame
//! `path-utils.ts` / `road/geometry.ts` pair.
//!
//! Authoring paths (`path="x z x z …"`, `<Segment via>`) are raw polylines;
//! carvers and water meshes expect a smoothed, evenly spaced station list:
//!
//! 1. [`chaikin_smooth`] rounds corners (corner-cutting, C0 → visually C1
//!    after two cuts) without overshooting like a spline through noisy
//!    authoring points.
//! 2. [`resample`] emits stations at a fixed arc-length spacing so per-station
//!    design (road profiles, river surfaces) is stable regardless of how far
//!    apart the authoring points were.

use bevy::math::Vec2;

/// Rounds polyline corners with two Chaikin cuts (VibeGame default
/// `smoothing = 2`). Keeps the first and last points of open paths; closed
/// paths (`closed = true`) wrap around. Paths with fewer than 3 points or
/// `iterations == 0` are returned unchanged.
pub fn chaikin_smooth(points: &[Vec2], iterations: u32, closed: bool) -> Vec<Vec2> {
    let mut out = points.to_vec();
    if points.len() < 3 || iterations == 0 {
        return out;
    }
    for _ in 0..iterations {
        out = chaikin_once(&out, closed);
    }
    out
}

fn chaikin_once(points: &[Vec2], closed: bool) -> Vec<Vec2> {
    let n = points.len();
    let mut out = Vec::with_capacity(n * 2);
    let segments = if closed { n } else { n - 1 };
    if !closed {
        out.push(points[0]);
    }
    for i in 0..segments {
        let a = points[i];
        let b = points[(i + 1) % n];
        out.push(a * 0.75 + b * 0.25);
        out.push(a * 0.25 + b * 0.75);
    }
    if !closed {
        out.push(points[n - 1]);
    }
    out
}

/// Total polyline length (meters).
pub fn path_length(points: &[Vec2]) -> f32 {
    points
        .windows(2)
        .map(|w| w[1].distance(w[0]))
        .sum::<f32>()
        .max(0.0)
}

/// Resamples a polyline into stations every `spacing` meters (arc length).
///
/// The first and last authoring points are always stations, so endpoints stay
/// exact (road approaches, river sources). `spacing <= 0` returns the input.
pub fn resample(points: &[Vec2], spacing: f32) -> Vec<Vec2> {
    if points.len() < 2 || spacing <= 0.0 || !spacing.is_finite() {
        return points.to_vec();
    }
    let mut out = vec![points[0]];
    let mut carried = 0.0_f32; // distance since the last emitted station
    for window in points.windows(2) {
        let (a, b) = (window[0], window[1]);
        let mut seg = a.distance(b);
        if seg <= 1e-9 {
            continue;
        }
        let dir = (b - a) / seg;
        while carried + seg >= spacing {
            let advance = spacing - carried;
            let p = out.last().expect("resample always emitted the first point") + dir * advance;
            out.push(p);
            seg -= advance;
            carried = 0.0;
        }
        carried += seg;
    }
    // Always pin the exact end point (unless the last station already sits on
    // it within a fraction of the spacing). Short paths always keep both ends.
    let end = points[points.len() - 1];
    let last = out.last().copied();
    let pin = match last {
        None => true,
        Some(l) => l.distance(end) > spacing * 0.25 || out.len() < 2,
    };
    if pin {
        out.push(end);
    }
    out
}

/// Distance from `p` to the closest point on the polyline `points`.
pub fn distance_to_path(points: &[Vec2], p: Vec2) -> f32 {
    nearest_on_path(points, p)
        .map(|(q, _)| q.distance(p))
        .unwrap_or(f32::INFINITY)
}

/// Closest point on the polyline plus the index of its segment start vertex.
/// `None` for paths with fewer than 2 points.
pub fn nearest_on_path(points: &[Vec2], p: Vec2) -> Option<(Vec2, usize)> {
    if points.len() < 2 {
        return points.first().map(|q| (*q, 0));
    }
    let mut best = (f32::INFINITY, Vec2::ZERO, 0usize);
    for (i, window) in points.windows(2).enumerate() {
        let (a, b) = (window[0], window[1]);
        let ab = b - a;
        let t = if ab.length_squared() <= 1e-12 {
            0.0
        } else {
            ((p - a).dot(ab) / ab.length_squared()).clamp(0.0, 1.0)
        };
        let q = a + ab * t;
        let d = q.distance_squared(p);
        if d < best.0 {
            best = (d, q, i);
        }
    }
    Some((best.1, best.2))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: Vec2, b: Vec2) -> bool {
        a.distance(b) < 1e-4
    }

    #[test]
    fn test_chaikin_keeps_short_paths() {
        let pts = vec![Vec2::ZERO, Vec2::X];
        assert_eq!(chaikin_smooth(&pts, 2, false), pts);
        assert_eq!(chaikin_smooth(&pts, 0, false), pts);
    }

    #[test]
    fn test_chaikin_keeps_endpoints_on_open_paths() {
        let pts = vec![Vec2::ZERO, Vec2::new(10.0, 0.0), Vec2::new(10.0, 10.0)];
        let out = chaikin_smooth(&pts, 2, false);
        assert!(close(out[0], pts[0]), "first point kept");
        assert!(
            close(*out.last().expect("non-empty"), pts[2]),
            "last point kept"
        );
        // Each cut doubles the segments: 2 -> 4 -> 8, plus the two pinned ends.
        assert_eq!(out.len(), 12);
    }

    #[test]
    fn test_chaikin_pulls_the_corner_inward() {
        // A right-angle knee: the smoothed path must not pass through the
        // corner any more (corner-cutting, no spline overshoot).
        let pts = vec![Vec2::ZERO, Vec2::new(10.0, 0.0), Vec2::new(10.0, 10.0)];
        let out = chaikin_smooth(&pts, 2, false);
        let near_corner = out
            .iter()
            .filter(|p| (p.x - 10.0).abs() < 0.5 && (p.y - 0.0).abs() < 0.5)
            .count();
        assert_eq!(near_corner, 0, "corner must be cut");
    }

    #[test]
    fn test_chaikin_closed_wraps() {
        let square = [
            Vec2::ZERO,
            Vec2::new(10.0, 0.0),
            Vec2::new(10.0, 10.0),
            Vec2::new(0.0, 10.0),
        ];
        let out = chaikin_smooth(&square, 1, true);
        assert_eq!(out.len(), 8, "closed path keeps segment count");
        // Close both loops explicitly so the lengths are comparable.
        let mut before = square.to_vec();
        before.push(square[0]);
        let mut closed_out = out.clone();
        closed_out.push(out[0]);
        assert!(
            path_length(&closed_out) < path_length(&before),
            "corner cutting shortens the loop"
        );
    }

    #[test]
    fn test_path_length_and_resample_degenerate() {
        assert_eq!(path_length(&[]), 0.0);
        assert_eq!(path_length(&[Vec2::ZERO]), 0.0);
        // spacing <= 0 returns the input unchanged.
        let pts = vec![Vec2::ZERO, Vec2::new(3.0, 4.0)];
        assert_eq!(resample(&pts, 0.0), pts);
    }

    #[test]
    fn test_resample_station_spacing_is_uniform() {
        let pts = vec![Vec2::ZERO, Vec2::new(100.0, 0.0)];
        let spacing = 7.0;
        let out = resample(&pts, spacing);
        assert!(close(out[0], pts[0]) && close(*out.last().expect("non-empty"), pts[1]));
        for (i, w) in out.windows(2).enumerate() {
            let d = w[0].distance(w[1]);
            let is_tail = i == out.len() - 2;
            assert!(
                (d - spacing).abs() < 1e-3 || (is_tail && d < spacing),
                "stations are {spacing} m apart (tail may be short), got {d}"
            );
        }
        assert_eq!(out.len(), 16, "15 stations of 7 m + the pinned end");
    }

    #[test]
    fn test_resample_pinches_endpoints_exactly() {
        // Short path: still yields [start, end] with the exact endpoints.
        let pts = vec![Vec2::new(1.0, 1.0), Vec2::new(2.0, 1.0)];
        let out = resample(&pts, 5.0);
        assert_eq!(out.len(), 2);
        assert!(close(out[0], pts[0]) && close(out[1], pts[1]));
    }

    #[test]
    fn test_resample_bends_follow_the_polyline() {
        let pts = vec![Vec2::ZERO, Vec2::new(10.0, 0.0), Vec2::new(10.0, 10.0)];
        let out = resample(&pts, 2.5);
        // Total length 20 m -> 8 stations of 2.5 m + end pin (2.5 m short? no:
        // 20/2.5 = 8 exact segments -> 9 stations, end already pinned).
        assert_eq!(out.len(), 9);
        assert!(out.iter().any(|p| (p.x - 10.0).abs() < 1e-3 && p.y > 0.5));
    }

    #[test]
    fn test_nearest_on_path_distance_and_segment() {
        let pts = vec![Vec2::ZERO, Vec2::new(10.0, 0.0), Vec2::new(10.0, 10.0)];
        let (q, seg) = nearest_on_path(&pts, Vec2::new(5.0, 3.0)).expect("open path");
        assert!(close(q, Vec2::new(5.0, 0.0)));
        assert_eq!(seg, 0);
        let (q, seg) = nearest_on_path(&pts, Vec2::new(14.0, 4.0)).expect("open path");
        assert!(close(q, Vec2::new(10.0, 4.0)));
        assert_eq!(seg, 1);
        assert_eq!(distance_to_path(&pts, Vec2::new(5.0, 3.0)), 3.0);
    }

    #[test]
    fn test_nearest_on_path_endpoint_clamps() {
        let pts = vec![Vec2::ZERO, Vec2::new(10.0, 0.0)];
        let (q, _) = nearest_on_path(&pts, Vec2::new(-4.0, 0.0)).expect("open path");
        assert!(close(q, Vec2::ZERO), "projected before the start clamps");
    }

    #[test]
    fn test_nearest_on_path_single_point() {
        let pts = vec![Vec2::new(3.0, 4.0)];
        let (q, seg) = nearest_on_path(&pts, Vec2::ZERO).expect("degenerate path");
        assert!(close(q, pts[0]));
        assert_eq!(seg, 0);
        assert!(nearest_on_path(&[], Vec2::ZERO).is_none());
    }
}
