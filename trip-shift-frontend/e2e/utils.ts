export const cfg = {
  BASE_URL: process.env.VITE_API_BASE_URL || 'http://localhost:8002',
  ROUTE_ID: process.env.VITE_TEST_ROUTE_ID || '55f151ad-63f8-40dc-85e7-330becb51c75',
  DAY: process.env.VITE_DAY || 'monday',
  EMAIL: process.env.TEST_LOGIN_EMAIL || '',
  PASSWORD: process.env.TEST_LOGIN_PASSWORD || '',
  TOKEN: process.env.TEST_API_TOKEN || '',
};


