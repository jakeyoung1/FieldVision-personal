// Tailwind build config. The compiled CSS is committed at static/css/tailwind.css,
// so the server and CI need no Node. Rebuild after adding Tailwind classes:
//   scripts/build_css.sh
module.exports = {
  content: ['./index.html'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['Fira Code', 'monospace'],
      },
      colors: {
        navy: '#002147',
        fvred: '#C8102E',
      },
    },
  },
};
