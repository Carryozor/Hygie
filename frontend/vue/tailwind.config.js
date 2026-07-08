/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg:     '#0f1117',
        bg2:    '#1a1d27',
        bg3:    '#212a38',
        accent: '#22c1d6',
        muted:  '#8b96b3',
        danger: '#ef4444',
        success:'#22c55e',
      },
    },
  },
  plugins: [],
}
