/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
    // Include shared packages for class detection
    "./node_modules/@shizuha/ui/dist/**/*.js",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
