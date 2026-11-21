// Which pages the extension can drive: ordinary web pages. Chrome does not let extensions attach to its own pages or the Web Store.
export function canInject(url) {
  return /^(https?|file|ftp):/i.test(url || "") && !/^https:\/\/(chrome\.google\.com\/webstore|chromewebstore\.google\.com)/i.test(url);
}
