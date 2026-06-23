import { showDialog } from './utils.js';

const _expiryInputConfigured = new WeakSet();

function padDatetimePart(n) {
  return String(n).padStart(2, '0');
}

function formatDateForDatetimeLocal(d) {
  return d.getFullYear() + '-' + padDatetimePart(d.getMonth() + 1) + '-' + padDatetimePart(d.getDate())
    + 'T' + padDatetimePart(d.getHours()) + ':' + padDatetimePart(d.getMinutes()) + ':' + padDatetimePart(d.getSeconds());
}

function minExpiryDatetimeLocal() {
  return formatDateForDatetimeLocal(new Date());
}

function defaultExpiryDatetimeLocal() {
  const d = new Date();
  d.setDate(d.getDate() + 30);
  d.setHours(23, 59, 59, 0);
  return formatDateForDatetimeLocal(d);
}

function toLocalDatetimeInput(isoStr) {
  if (!isoStr) return '';
  const normalized = isoStr.endsWith('Z') ? isoStr : isoStr + 'Z';
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return '';
  return formatDateForDatetimeLocal(d);
}

function configureExpiryDateInput(input, { value, applyDefault = false } = {}) {
  if (!input) return;
  input.step = '1';
  input.min = minExpiryDatetimeLocal();
  if (value !== undefined) {
    input.value = value;
  } else if (applyDefault) {
    input.value = defaultExpiryDatetimeLocal();
  }
  if (!_expiryInputConfigured.has(input)) {
    _expiryInputConfigured.add(input);
    input.addEventListener('change', () => {
      input.min = minExpiryDatetimeLocal();
      if (input.value && input.min && input.value < input.min) {
        input.value = input.min;
      }
    });
  }
}

function readExpiryDateFromInput(inputId) {
  const input = document.getElementById(inputId);
  if (!input?.value) return null;
  input.min = minExpiryDatetimeLocal();
  if (input.value < input.min) {
    showDialog('Expiration must be in the future.', 'Invalid expiration');
    return undefined;
  }
  return new Date(input.value).toISOString().replace('Z', '');
}

export {
  padDatetimePart,
  formatDateForDatetimeLocal,
  minExpiryDatetimeLocal,
  defaultExpiryDatetimeLocal,
  toLocalDatetimeInput,
  configureExpiryDateInput,
  readExpiryDateFromInput,
};
