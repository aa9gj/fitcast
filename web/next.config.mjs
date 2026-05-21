/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The PWA service worker is hand-rolled (public/sw.js) and registered in
  // the root layout — no build plugin needed.
};

export default nextConfig;
