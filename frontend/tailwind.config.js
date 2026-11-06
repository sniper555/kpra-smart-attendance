/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: { 950: "#07111f", 900: "#0B1F3A", 800: "#123056", 700: "#1a406f" },
        brand: { 50: "#f4f7fb", 100: "#e6eef6", 600: "#1D4E89", 700: "#17406f" },
      },
    },
  },
  plugins: [],
};
