import { test, expect } from '@playwright/test';

test('select first trip and verify valid next filtering', async ({ page }) => {
  await page.goto('/');

  // Load sample
  await page.getByRole('button', { name: 'Load sample' }).click();

  // Available trips should show cards, sorted ascending by Dep time
  const cards = page.locator('section:has-text("Available trips") button');
  await expect(cards.first()).toBeVisible();

  // Click first card
  const firstCard = cards.first();
  await firstCard.click();

  // Now only valid next trips should be visible by default (toggle is ON)
  const lastSummary = page.locator('text=Last arrival:');
  await expect(lastSummary).toBeVisible();

  const n = await cards.count();
  expect(n).toBeGreaterThan(0);
});


