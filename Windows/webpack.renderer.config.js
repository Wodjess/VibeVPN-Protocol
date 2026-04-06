// Renderer doesn't need native module loaders (node-loader, asset-relocator)
// — those use __dirname which is unavailable in the sandboxed renderer.
const rendererRules = require('./webpack.rules').filter(
  (rule) => !String(rule.test).includes('native_modules') &&
            !String(rule.test).includes('node_modules')
);

rendererRules.push({
  test: /\.css$/,
  use: [{ loader: 'style-loader' }, { loader: 'css-loader' }],
});

module.exports = {
  module: {
    rules: rendererRules,
  },
};
