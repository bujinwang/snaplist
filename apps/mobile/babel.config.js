// Plain CommonJS — babel reads this before any TypeScript transform exists.
module.exports = function (api) {
  api.cache(true);
  return {
    presets: ["babel-preset-expo"],
  };
};
