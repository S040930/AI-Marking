import '@testing-library/jest-dom/vitest';

// 当前 Node 环境的 localStorage 是实验性特性（需 --localstorage-file），
// 测试环境下不可用。为覆盖访问令牌存取逻辑，提供内存实现。
const storage = new Map<string, string>();
const localStorageMock: Storage = {
  get length() {
    return storage.size;
  },
  clear() {
    storage.clear();
  },
  getItem(key: string) {
    return storage.get(key) ?? null;
  },
  key(index: number) {
    return [...storage.keys()][index] ?? null;
  },
  removeItem(key: string) {
    storage.delete(key);
  },
  setItem(key: string, value: string) {
    storage.set(key, String(value));
  },
};

Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
  writable: true,
});

// jsdom 未实现 scrollIntoView;对话转录自动滚动依赖它。
if (typeof Element !== 'undefined' && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => undefined;
}

// jsdom 未实现 matchMedia;sonner(Toaster)等组件在 effect 中查询暗色模式依赖它。
if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
