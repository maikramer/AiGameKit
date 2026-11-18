# Vendored — snapshot do `bevy_ext_gltf`

Cópia do crate `crates/bevy_ext_gltf` do checkout irmão
[`bevy_extensions`](https://github.com/maikramer/bevy_extensions) no commit
`063794d` (2026-11-16), com o `Cargo.toml` convertido para standalone.

Motivo: o Studio tem de compilar sozinho e o working tree do irmão estava a
meio de uma refactorização (módulos `decode`/`pool`, versão 0.1.0) que não
compilava. Este snapshot é exactamente o 0.1.1 que o app usava.

Licença original: MIT OR Apache-2.0 (ver `LICENSE-MIT`/`LICENSE-APACHE`).
Quando a refactorização do irmão estabilizar, trocar o path dep do
`Studio/Cargo.toml` de volta para
`../../bevy_extensions/crates/bevy_ext_gltf` e apagar este diretório.
