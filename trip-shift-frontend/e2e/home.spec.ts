import { test, expect } from '@playwright/test';

test('home loads as index; nav disabled until login; icon link works', async ({ page }) => {
  await page.goto('/');
  // Home headings visible
  await expect(page.getByText('Welcome to Trip Shift Planner').or(page.getByText('Benvenuto in Trip Shift Planner')).or(page.getByText('Bienvenue dans Trip Shift Planner')).or(page.getByText('Willkommen beim Trip Shift Planner'))).toBeVisible();

  // Nav items should be disabled when logged out (look inside header nav)
  const nav = page.locator('header nav');
  await expect(nav.getByText('Shifts')).toHaveAttribute('aria-disabled', 'true');

  // CTA visible with Login and Register in Home panel
  const main = page.getByRole('main');
  await expect(main.getByRole('button', { name: 'Login' })).toBeVisible();
  await expect(main.getByRole('button', { name: 'Register' })).toBeVisible();

  // Icon link still points to /home (click does not navigate away)
  await page.locator('header img[alt="Elettra"]').click();
  await expect(page).toHaveURL(/\/home|\/$/);
});


