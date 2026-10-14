/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://api:8000/api/:path*",
      },
      {
        source: "/static/:path*",
        destination: "http://api:8000/static/:path*",
      },
    ];
  },
};
export default nextConfig;
