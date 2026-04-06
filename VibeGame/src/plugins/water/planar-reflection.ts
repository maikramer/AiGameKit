import * as THREE from 'three';

export class PlanarReflection {
  private renderTarget: THREE.WebGLRenderTarget;
  private mirrorCamera: THREE.PerspectiveCamera;
  private reflectionPlane = new THREE.Plane();
  private q = new THREE.Vector4();
  private projectionMatrix = new THREE.Matrix4();

  constructor(width: number = 512, height: number = 512) {
    this.renderTarget = new THREE.WebGLRenderTarget(width, height, {
      minFilter: THREE.LinearFilter,
      magFilter: THREE.LinearFilter,
      format: THREE.RGBAFormat,
      generateMipmaps: false,
    });

    this.mirrorCamera = new THREE.PerspectiveCamera();
  }

  get texture(): THREE.Texture {
    return this.renderTarget.texture;
  }

  resize(width: number, height: number): void {
    this.renderTarget.setSize(width, height);
  }

  render(
    renderer: THREE.WebGLRenderer,
    scene: THREE.Scene,
    camera: THREE.PerspectiveCamera,
    waterLevel: number
  ): void {
    this.reflectionPlane.set(new THREE.Vector3(0, 1, 0), -waterLevel);

    this.mirrorCamera.position.copy(camera.position);
    this.mirrorCamera.rotation.copy(camera.rotation);
    this.mirrorCamera.aspect = camera.aspect;
    this.mirrorCamera.fov = camera.fov;
    this.mirrorCamera.near = camera.near;
    this.mirrorCamera.far = camera.far;

    this.mirrorCamera.position.y -=
      2 * (this.mirrorCamera.position.y - waterLevel);
    this.mirrorCamera.rotation.x = -camera.rotation.x;
    this.mirrorCamera.rotation.z = -camera.rotation.z;

    this.mirrorCamera.updateProjectionMatrix();
    this.mirrorCamera.updateMatrixWorld();

    this.reflectionPlane.applyMatrix4(this.mirrorCamera.matrixWorldInverse);

    this.computeObliqueProjection();

    const currentRenderTarget = renderer.getRenderTarget();
    const currentXrEnabled = renderer.xr.enabled;

    renderer.xr.enabled = false;
    renderer.setRenderTarget(this.renderTarget);
    renderer.setClearColor(0x000000, 0);
    renderer.clear();

    scene.traverse(hideWaterMesh);
    renderer.render(scene, this.mirrorCamera);
    scene.traverse(showWaterMesh);

    renderer.setRenderTarget(currentRenderTarget);
    renderer.xr.enabled = currentXrEnabled;
  }

  private planeDotVec4(plane: THREE.Plane, v: THREE.Vector4): number {
    return (
      plane.normal.x * v.x +
      plane.normal.y * v.y +
      plane.normal.z * v.z +
      plane.constant * v.w
    );
  }

  private computeObliqueProjection(): void {
    const projMatrix = this.mirrorCamera.projectionMatrix.clone();

    this.q.set(
      (Math.sign(this.reflectionPlane.normal.x) + projMatrix.elements[8]) /
        projMatrix.elements[0],
      (Math.sign(this.reflectionPlane.normal.y) + projMatrix.elements[9]) /
        projMatrix.elements[5],
      -1.0,
      (1.0 + projMatrix.elements[10]) / projMatrix.elements[14]
    );
    this.q.multiplyScalar(
      2.0 / this.planeDotVec4(this.reflectionPlane, this.q)
    );

    this.projectionMatrix.copy(projMatrix);
    this.projectionMatrix.elements[2] = this.q.x;
    this.projectionMatrix.elements[6] = this.q.y;
    this.projectionMatrix.elements[10] = this.q.z;
    this.projectionMatrix.elements[14] = this.q.w;

    this.mirrorCamera.projectionMatrix.copy(this.projectionMatrix);
  }

  dispose(): void {
    this.renderTarget.dispose();
  }
}

function hideWaterMesh(object: THREE.Object3D): void {
  if ((object as THREE.Mesh).isMesh && object.userData._isWater) {
    object.userData._waterVisibleBackup = object.visible;
    object.visible = false;
  }
}

function showWaterMesh(object: THREE.Object3D): void {
  if ((object as THREE.Mesh).isMesh && object.userData._isWater) {
    object.visible = object.userData._waterVisibleBackup ?? true;
    delete object.userData._waterVisibleBackup;
  }
}
