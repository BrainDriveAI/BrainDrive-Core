const tsJest = require('ts-jest').default;

const tsJestTransformer = tsJest.createTransformer({
  isolatedModules: true,
});

module.exports = {
  process(src, filename, config, options) {
    let source = src;

    if (/\.[jt]sx?$/.test(filename)) {
      source = source
        .replace(/\bimport\.meta\.env\b/g, 'globalThis.__VITE_ENV__')
        .replace(/\bimport\.meta\b/g, 'globalThis.__IMPORT_META__');
    }

    return tsJestTransformer.process(source, filename, config, options);
  },
};
