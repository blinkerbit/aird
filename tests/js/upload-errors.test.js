import { describe, expect, it } from 'vitest';
import { friendlyUploadErrorMessage } from '/static/js/browse/upload-errors.js';

describe('friendlyUploadErrorMessage', () => {
  it('passes through cancelled and empty', () => {
    expect(friendlyUploadErrorMessage(new Error('cancelled'))).toBe('cancelled');
    expect(friendlyUploadErrorMessage(new Error(''))).toBe('');
  });

  it('surfaces ABAC JSON reason (upload-not-allowed regression)', () => {
    const msg = JSON.stringify({
      error: 'Access denied',
      reason: 'No matching permit policy (default deny)',
    });
    expect(friendlyUploadErrorMessage(new Error(msg))).toBe(
      'Upload denied: No matching permit policy (default deny)'
    );
  });

  it('maps bare access denied to policy guidance', () => {
    expect(friendlyUploadErrorMessage(new Error('Access denied'))).toMatch(/file\.write/);
    expect(friendlyUploadErrorMessage(new Error('HTTP 403'))).toMatch(/access policy/i);
  });

  it('maps network errors', () => {
    expect(friendlyUploadErrorMessage(new Error('Network error during upload'))).toMatch(
      /connection/i
    );
  });

  it('maps proxy 413 entity too large', () => {
    expect(friendlyUploadErrorMessage(new Error('Request Entity Too Large'))).toMatch(/nginx/i);
  });

  it('keeps short raw server messages', () => {
    expect(friendlyUploadErrorMessage(new Error('Disk full'))).toBe('Disk full');
  });
});
