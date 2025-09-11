import { test, expect } from '@playwright/test';
import { cfg } from './utils';

test('login (or use token) and load trips by route+day', async ({ page }) => {
  test.skip(!cfg.BASE_URL || !cfg.ROUTE_ID, 'BASE_URL/ROUTE_ID required');

  await page.goto('/');

  // Fill backend panel
  await page.getByPlaceholder('Base URL e.g., http://localhost:8002').fill(cfg.BASE_URL);
  await page.getByPlaceholder('Route ID').fill(cfg.ROUTE_ID);
  await page.getByRole('combobox').selectOption(cfg.DAY);

  if (cfg.TOKEN) {
    await page.getByPlaceholder('Paste Bearer token (optional)').fill(cfg.TOKEN);
  } else {
    // login with email/password
    await page.getByPlaceholder('Email').fill(cfg.EMAIL);
    await page.getByPlaceholder('Password').fill(cfg.PASSWORD);
    await page.getByRole('button', { name: 'Login' }).click();
  }

  await page.getByRole('button', { name: 'Load trips by route + day' }).click();

  // Assert trips loaded label visible
  await expect(page.locator('text=Trips loaded:')).toBeVisible();
});


