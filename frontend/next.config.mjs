/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Pin the workspace root to this directory. The Python repo root has its own
  // package.json (the SimuLang replay runtime) and there can be a stray lockfile
  // in $HOME, so Next/Turbopack would otherwise infer the wrong workspace root
  // and fail page-data collection. Anchor module resolution to the frontend.
  turbopack: {
    root: import.meta.dirname,
  },
  // Allow remote screenshot thumbnails from the mock data source.
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "**" },
      { protocol: "http", hostname: "localhost" },
    ],
  },
};

export default nextConfig;
