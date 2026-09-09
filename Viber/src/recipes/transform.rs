//! Transform math helpers with Bevy semantics.

use bevy::math::{EulerRot, Quat};

/// Euler angles in degrees (XYZ intrinsic order) to a quaternion `[x, y, z, w]`.
pub fn euler_deg_to_quat(euler_deg: [f32; 3]) -> [f32; 4] {
    let q = Quat::from_euler(
        EulerRot::XYZ,
        euler_deg[0].to_radians(),
        euler_deg[1].to_radians(),
        euler_deg[2].to_radians(),
    );
    [q.x, q.y, q.z, q.w]
}

/// Euler angles in RADIANS (XYZ intrinsic order) to a quaternion `[x, y, z, w]`.
///
/// The `rotation` attribute carries radians when it has 3 components (the
/// VibeGame composition convention) and a raw quaternion with 4 —
/// `parse_common` routes between this and [`euler_deg_to_quat`].
pub fn euler_rad_to_quat(euler_rad: [f32; 3]) -> [f32; 4] {
    let q = Quat::from_euler(EulerRot::XYZ, euler_rad[0], euler_rad[1], euler_rad[2]);
    [q.x, q.y, q.z, q.w]
}

/// Apply a quaternion to a vector (used by unit tests).
#[cfg(test)]
pub fn quat_apply(quat_xyzw: [f32; 4], v: [f32; 3]) -> [f32; 3] {
    let q = Quat::from_xyzw(quat_xyzw[0], quat_xyzw[1], quat_xyzw[2], quat_xyzw[3]);
    (q * bevy::math::Vec3::from_array(v)).to_array()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f32, b: f32) -> bool {
        (a - b).abs() < 1e-5
    }

    #[test]
    fn test_euler_identity() {
        let q = euler_deg_to_quat([0.0, 0.0, 0.0]);
        assert!(approx(q[3], 1.0));
        assert!(approx(q[0], 0.0) && approx(q[1], 0.0) && approx(q[2], 0.0));
    }

    #[test]
    fn test_euler_90_degrees_around_z_maps_x_to_y() {
        let q = euler_deg_to_quat([0.0, 0.0, 90.0]);
        let out = quat_apply(q, [1.0, 0.0, 0.0]);
        assert!(approx(out[0], 0.0), "{out:?}");
        assert!(approx(out[1], 1.0), "{out:?}");
    }

    #[test]
    fn test_euler_180_degrees_around_y_negates_x() {
        let q = euler_deg_to_quat([0.0, 180.0, 0.0]);
        let out = quat_apply(q, [1.0, 0.0, 0.0]);
        assert!(approx(out[0], -1.0), "{out:?}");
    }

    #[test]
    fn test_euler_produces_unit_quaternion() {
        let q = euler_deg_to_quat([33.0, -12.5, 210.0]);
        let norm: f32 = (q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]).sqrt();
        assert!(approx(norm, 1.0), "{q:?}");
    }

    #[test]
    fn test_euler_rad_matches_deg() {
        // π/2 rad em Z == 90° em Z.
        let a = euler_rad_to_quat([0.0, 0.0, std::f32::consts::FRAC_PI_2]);
        let b = euler_deg_to_quat([0.0, 0.0, 90.0]);
        for (x, y) in a.iter().zip(b.iter()) {
            assert!(approx(*x, *y), "{a:?} vs {b:?}");
        }
    }

    #[test]
    fn test_euler_rad_rotates_x_to_y() {
        let q = euler_rad_to_quat([0.0, 0.0, std::f32::consts::FRAC_PI_2]);
        let out = quat_apply(q, [1.0, 0.0, 0.0]);
        assert!(approx(out[0], 0.0), "{out:?}");
        assert!(approx(out[1], 1.0), "{out:?}");
    }
}
