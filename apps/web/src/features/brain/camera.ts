export interface Point {
  x: number;
  y: number;
}

export interface Camera {
  x: number;
  y: number;
  zoom: number;
  rotation: number;
}

const MIN_ZOOM = 0.35;
const MAX_ZOOM = 3.2;

export function fitCamera(
  world: { width: number; height: number },
  viewport: { width: number; height: number },
): Camera {
  const zoom = Math.min(viewport.width / world.width, viewport.height / world.height) * 0.9;
  return {
    x: viewport.width / 2 - (world.width / 2) * zoom,
    y: viewport.height / 2 - (world.height / 2) * zoom,
    zoom,
    rotation: 0,
  };
}

export function panCamera(camera: Camera, dx: number, dy: number): Camera {
  return { ...camera, x: camera.x + dx, y: camera.y + dy };
}

export function screenToWorld(point: Point, camera: Camera): Point {
  return { x: (point.x - camera.x) / camera.zoom, y: (point.y - camera.y) / camera.zoom };
}

export function zoomCamera(camera: Camera, factor: number, anchor: Point): Camera {
  const before = screenToWorld(anchor, camera);
  const zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, camera.zoom * factor));
  return {
    ...camera,
    zoom,
    x: anchor.x - before.x * zoom,
    y: anchor.y - before.y * zoom,
  };
}
