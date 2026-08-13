import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    // getUserMedia is blocked on plain HTTP everywhere except localhost, so
    // development binds to localhost by design. To test from a phone on the
    // LAN you need `--host` AND HTTPS — a plain LAN IP over HTTP will not get
    // microphone access (PLAN, browser constraints).
    host: 'localhost',
    port: 5173,
  },
  build: {
    target: 'es2022',
    outDir: 'dist',
  },
});
