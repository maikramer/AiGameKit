export const MINIMAL_ROOM_SCHEMA = `
// @colyseus/schema opcional — esta sala pode usar apenas room.send("transform", msg)
//
// Mensagem "transform" ampliada (posição + rotação quaternion + escala):
//   { eid, x, y, z, rotX, rotY, rotZ, rotW, scaleX, scaleY, scaleZ }
`;
