import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

import { expect, test } from '@playwright/test';


const fixture = fileURLToPath(new URL('../../tests/fixtures/e2e/Song.ttml', import.meta.url));

test('upload, preview, change selection, apply and download exact TTML output', async ({ page }) => {
  await page.goto('/upload');
  await page.locator('input[type="file"]').setInputFiles(fixture);
  await expect(page.getByText('Song.ttml').first()).toBeVisible();

  await page.getByRole('button', { name: '开始预览' }).click();
  await expect(page).toHaveURL(/\/preview$/);
  await expect(page.getByTestId('source-switcher')).toContainText('QQ 音乐');
  await expect(page.getByTestId('candidate-row')).toHaveCount(2);

  await page.getByTestId('candidate-row').filter({ hasText: 'qq-best' }).click();
  await page.getByTestId('candidate-row').filter({ hasText: 'qq-alt' }).click();
  await expect(page.getByTestId('write-preview-context')).toContainText('qq-alt');

  const apply = page.getByRole('button', { name: '写入并生成结果' });
  await expect(apply).toBeEnabled();
  await apply.click();
  await expect(page).toHaveURL(/\/result$/);
  await expect(page.getByTestId('result-file-review')).toContainText('Song.ttml');

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: '单文件下载' }).click();
  const download = await downloadPromise;
  const downloadedPath = await download.path();
  expect(downloadedPath).not.toBeNull();
  const output = await readFile(downloadedPath!, 'utf-8');
  expect(output).toContain('key="qqMusicId" value="qq-alt"');
  expect(output).toContain('key="qqMusicId" value="qq-mid-alt"');
});
