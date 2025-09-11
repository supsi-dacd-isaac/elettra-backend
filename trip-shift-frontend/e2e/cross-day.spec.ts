import { test, expect } from '@playwright/test';

test('shows D+1 prefix for times beyond 24h', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Load sample' }).click();

  // Search for overnight sample
  await page.getByPlaceholder('Search text').fill('Overnight Hub');
  const card = page.locator('section:has-text("Available trips") button');
  await expect(card).toHaveCount(1);

  const text = await card.textContent();
  expect(text || '').toContain('D+1 01:10');
});


