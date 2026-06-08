/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        heading: ['Noto Serif SC', 'Songti SC', 'serif'],
        body: ['Noto Sans SC', 'PingFang SC', 'SF Pro Display', 'Helvetica Neue', 'sans-serif'],
        mono: ['JetBrains Mono', 'SF Mono', 'Consolas', 'monospace'],
      },
      colors: {
        bg: {
          DEFAULT: '#F7F6F3',
          secondary: '#F0EFEC',
          tertiary: '#F0EFEC',
        },
        card: {
          DEFAULT: '#FFFFFF',
          hover: '#F9F9F8',
        },
        text: {
          DEFAULT: '#111111',
          secondary: '#787774',
          tertiary: '#A09E9A',
          placeholder: '#BFBDB9',
        },
        border: {
          DEFAULT: '#EAEAEA',
          light: '#F0F0EE',
        },
        divider: '#EEEEEC',
        accent: {
          DEFAULT: '#1F6C9F',
          bg: '#E1F3FE',
        },
        success: {
          DEFAULT: '#346538',
          bg: '#EDF3EC',
        },
        danger: {
          DEFAULT: '#9F2F2D',
          bg: '#FDEBEC',
        },
        warning: {
          DEFAULT: '#956400',
          bg: '#FBF3DB',
        },
      },
      spacing: {
        'xs': '4px',
        'sm': '8px',
        'md': '16px',
        'lg': '24px',
        'xl': '32px',
        '2xl': '48px',
        '3xl': '64px',
      },
      borderRadius: {
        sm: '4px',
        md: '8px',
        lg: '12px',
      },
      lineHeight: {
        tight: '1.3',
        normal: '1.6',
        loose: '1.8',
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
}
