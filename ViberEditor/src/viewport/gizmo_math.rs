//! Pure math for the transform gizmo: screen-space hit tests, the drag parameter along an axis,
//! the angle around a ring, and snapping. No ECS — unit-tested on its own.

use bevy::math::{Vec2, Vec3};

/// Distance from `p` to the segment `a`–`b` (screen pixels).
pub fn distance_to_segment(p: Vec2, a: Vec2, b: Vec2) -> f32 {
    let ab = b - a;
    let len2 = ab.length_squared();
    if len2 < 1e-9 {
        return p.distance(a);
    }
    let t = ((p - a).dot(ab) / len2).clamp(0.0, 1.0);
    p.distance(a + ab * t)
}

/// Distance from `p` to a closed polyline (a projected ring).
pub fn distance_to_polyline(p: Vec2, points: &[Vec2]) -> f32 {
    points
        .iter()
        .zip(points.iter().cycle().skip(1))
        .map(|(a, b)| distance_to_segment(p, *a, *b))
        .fold(f32::INFINITY, f32::min)
}

/// Parameter `t` of the point on the line `origin + axis * t` closest to the ray
/// `ray_origin + ray_dir * s` (both directions normalized). `None` when they are parallel.
pub fn closest_on_axis(origin: Vec3, axis: Vec3, ray_origin: Vec3, ray_dir: Vec3) -> Option<f32> {
    let w = origin - ray_origin;
    let b = axis.dot(ray_dir);
    let denom = 1.0 - b * b;
    if denom.abs() < 1e-6 {
        return None;
    }
    let d = axis.dot(w);
    let e = ray_dir.dot(w);
    Some((b * e - d) / denom)
}

/// Signed angle (radians) of the ray's hit on the plane through `origin` with normal `axis`,
/// measured in a stable basis of that plane. `None` when the ray is parallel to the plane.
pub fn angle_on_plane(origin: Vec3, axis: Vec3, ray_origin: Vec3, ray_dir: Vec3) -> Option<f32> {
    let denom = axis.dot(ray_dir);
    if denom.abs() < 1e-6 {
        return None;
    }
    let t = axis.dot(origin - ray_origin) / denom;
    let hit = ray_origin + ray_dir * t - origin;
    let (u, v) = plane_basis(axis);
    Some(hit.dot(v).atan2(hit.dot(u)))
}

/// Two unit vectors spanning the plane with normal `axis` (right-handed: `u × v = axis`).
pub fn plane_basis(axis: Vec3) -> (Vec3, Vec3) {
    let helper = if axis.y.abs() < 0.9 { Vec3::Y } else { Vec3::X };
    let u = helper.cross(axis).normalize();
    let v = axis.cross(u);
    (u, v)
}

/// `value` rounded to the nearest multiple of `step` (no-op for `step <= 0`).
pub fn snap(value: f32, step: f32) -> f32 {
    if step <= 0.0 {
        value
    } else {
        (value / step).round() * step
    }
}

/// Shortest signed difference `to - from` of two angles, in (-π, π].
pub fn angle_delta(from: f32, to: f32) -> f32 {
    let mut delta = to - from;
    while delta > std::f32::consts::PI {
        delta -= std::f32::consts::TAU;
    }
    while delta <= -std::f32::consts::PI {
        delta += std::f32::consts::TAU;
    }
    delta
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::f32::consts::{FRAC_PI_2, PI};

    #[test]
    fn segment_distance() {
        let (a, b) = (Vec2::ZERO, Vec2::new(10.0, 0.0));
        assert_eq!(distance_to_segment(Vec2::new(5.0, 3.0), a, b), 3.0);
        assert_eq!(
            distance_to_segment(Vec2::new(-4.0, 3.0), a, b),
            5.0,
            "clamped to the end"
        );
        assert_eq!(
            distance_to_segment(Vec2::new(2.0, 2.0), a, a),
            8f32.sqrt(),
            "degenerate"
        );
    }

    #[test]
    fn polyline_distance_closes_the_loop() {
        let square = [
            Vec2::ZERO,
            Vec2::new(10.0, 0.0),
            Vec2::new(10.0, 10.0),
            Vec2::new(0.0, 10.0),
        ];
        assert_eq!(
            distance_to_polyline(Vec2::new(-2.0, 5.0), &square),
            2.0,
            "closing edge"
        );
    }

    #[test]
    fn axis_parameter_follows_the_ray() {
        // Axis X through the origin; a ray looking down -Y from (3, 10, 0) hits x = 3.
        let t =
            closest_on_axis(Vec3::ZERO, Vec3::X, Vec3::new(3.0, 10.0, 0.0), Vec3::NEG_Y).unwrap();
        assert!((t - 3.0).abs() < 1e-5);
        // A skewed ray still projects to its closest approach.
        let dir = Vec3::new(0.0, -1.0, -1.0).normalize();
        let t = closest_on_axis(Vec3::ZERO, Vec3::X, Vec3::new(-2.0, 5.0, 5.0), dir).unwrap();
        assert!((t + 2.0).abs() < 1e-4);
        assert!(
            closest_on_axis(Vec3::ZERO, Vec3::X, Vec3::Y, Vec3::X).is_none(),
            "parallel"
        );
    }

    #[test]
    fn ring_angle_turns_with_the_hit() {
        let a = angle_on_plane(Vec3::ZERO, Vec3::Y, Vec3::new(1.0, 5.0, 0.0), Vec3::NEG_Y).unwrap();
        let b =
            angle_on_plane(Vec3::ZERO, Vec3::Y, Vec3::new(0.0, 5.0, -1.0), Vec3::NEG_Y).unwrap();
        assert!((angle_delta(a, b).abs() - FRAC_PI_2).abs() < 1e-4);
        assert!(
            angle_on_plane(Vec3::ZERO, Vec3::Y, Vec3::Y, Vec3::X).is_none(),
            "parallel"
        );
    }

    #[test]
    fn basis_is_orthonormal_and_right_handed() {
        for axis in [
            Vec3::X,
            Vec3::Y,
            Vec3::Z,
            Vec3::new(1.0, 2.0, 3.0).normalize(),
        ] {
            let (u, v) = plane_basis(axis);
            assert!(u.dot(axis).abs() < 1e-5 && v.dot(axis).abs() < 1e-5 && u.dot(v).abs() < 1e-5);
            assert!((u.cross(v) - axis).length() < 1e-5);
        }
    }

    #[test]
    fn snapping_and_wrapping() {
        assert_eq!(snap(1.26, 0.5), 1.5);
        assert_eq!(snap(-0.74, 0.5), -0.5);
        assert_eq!(snap(1.26, 0.0), 1.26);
        assert!(
            (angle_delta(PI - 0.1, -PI + 0.1) - 0.2).abs() < 1e-5,
            "wraps through ±π"
        );
    }
}
