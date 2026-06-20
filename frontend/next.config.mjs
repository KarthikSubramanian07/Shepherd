/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Allow remote screenshot thumbnails from the mock data source.
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "**" },
      { protocol: "http", hostname: "localhost" },
    ],
  },
};

export default nextConfig;
