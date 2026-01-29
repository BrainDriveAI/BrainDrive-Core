const path = require("path");
const HtmlWebpackPlugin = require("html-webpack-plugin");
const { ModuleFederationPlugin } = require("webpack").container;
const deps = require("./package.json").dependencies;

module.exports = {
  mode: "development",
  entry: "./src/index",
  output: {
    path: path.resolve(__dirname, 'dist'),
    // path: path.resolve(__dirname, "..", "..", "backend", "plugins", "shared", "BrainDriveChat", "v1.0.24", "dist"),
    publicPath: "auto",
    clean: true,
    library: {
      type: 'var',
      name: 'BrainDriveChat'
    }
  },
  resolve: {
    extensions: [".tsx", ".ts", ".js"],
  },
  module: {
    rules: [
      {
        test: /\.(ts|tsx)$/,
        use: "ts-loader",
        exclude: /node_modules/,
      },
      {
        test: /\.css$/,
        use: [
          'style-loader',
          'css-loader'
        ]
      }
    ],
  },
  plugins: [
    new ModuleFederationPlugin({
      name: "BrainDriveChat",
      library: { type: "var", name: "BrainDriveChat" },
      filename: "remoteEntry.js",
      exposes: {
        "./BrainDriveChat": "./src/index",
      },
      shared: {
        react: {
          singleton: true,
          requiredVersion: deps.react,
          eager: true
        },
        "react-dom": {
          singleton: true,
          requiredVersion: deps["react-dom"],
          eager: true
        }
      }
    }),
    new HtmlWebpackPlugin({
      template: "./public/index.html",
    }),
  ],
  devServer: {
    port: 3001,
    static: {
      directory: path.join(__dirname, "public"),
    },
    hot: true,
  },
};
