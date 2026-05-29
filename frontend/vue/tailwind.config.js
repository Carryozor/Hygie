/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg:     '#0f1117',
        bg2:    '#1a1d27',
        bg3:    '#22263a',
        accent: '#6366f1',
        muted:  '#8b92b3',
        danger: '#ef4444',
        success:'#22c55e',
      },
    },
  },
  plugins: [],
}
