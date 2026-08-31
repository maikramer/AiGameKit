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
}
