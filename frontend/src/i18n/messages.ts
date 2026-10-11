import { useI18n } from 'vue-i18n';

import { ApiError } from '@/api/client';

/**
 * User-facing text for API error codes and job failure codes, always drawn from the catalog
 * (FR-036): the server's English `message` / `failure_reason` is never the primary text.
 * Unknown codes fall back to `errors.generic`.
 */
export function useMessages() {
  const { t, te } = useI18n();

  function fromCatalog(key: string): string {
    return te(key) ? t(key) : t('errors.generic');
  }

  /** Catalog message for an API error code (`errors.codes.<code>`). */
  function errorCodeMessage(code: string): string {
    return fromCatalog(`errors.codes.${code}`);
  }

  /** Catalog message for a thrown error; non-API errors get the generic message. */
  function errorMessage(err: unknown): string {
    return err instanceof ApiError ? errorCodeMessage(err.code) : t('errors.generic');
  }

  /** Catalog message for a failed job's `failure_code` (`job.failures.<code>`). */
  function failureMessage(code: string | null | undefined): string {
    return code ? fromCatalog(`job.failures.${code}`) : t('errors.generic');
  }

  /** Whether the catalog has a message for this API error code. */
  function hasErrorCode(code: string): boolean {
    return te(`errors.codes.${code}`);
  }

  return { errorCodeMessage, errorMessage, failureMessage, hasErrorCode };
}
