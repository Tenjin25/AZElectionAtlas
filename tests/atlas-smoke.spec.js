const { test, expect } = require('@playwright/test');

test('Arizona atlas shell loads', async ({ page }) => {
  await page.goto('/index.html', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveTitle(/Arizona Election Atlas/);
  await expect(page.locator('#map')).toBeVisible();
  await expect(page.locator('.badge-state-name')).toHaveText('Arizona');
});
