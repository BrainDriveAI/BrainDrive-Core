import { z } from "zod";

// Add type declaration for Vite's import.meta.env
declare global {
	interface ImportMeta {
		env: Record<string, string>;
	}
}

// Environment configuration schema
const envSchema = z.object({
	VITE_API_URL: z.string().optional(),
	VITE_API_TIMEOUT: z.string().transform(Number).optional(),
	VITE_PLUGIN_INSTALL_TIMEOUT: z.string().transform(Number).optional(),
	VITE_USE_PROXY: z
		.string()
		.transform((val) => val !== "false")
		.default("true"), // Default to using proxy in development
	MODE: z.enum(["development", "production", "test"]).default("development"),
	VITE_PLUGIN_STUDIO_DEV_MODE: z
		.string()
		.transform((val) => val === "true")
		.default("false"),
	VITE_SHOW_EDITING_CONTROLS: z
		.string()
		.transform((val) => val === "true")
		.default("false"),
	VITE_DEV_AUTO_LOGIN: z
		.string()
		.transform((val) => val === "true")
		.optional(),
	VITE_DEV_EMAIL: z.string().optional(),
	VITE_DEV_PASSWORD: z.string().optional(),
});

// Parse environment variables with fallback for import.meta.env
const env = envSchema.parse(
	typeof import.meta !== "undefined" ? import.meta.env : {}
);

// Determine API URL based on environment and proxy configuration
const getApiBaseUrl = () => {
	// If environment variable is provided, use it (highest priority)
	if (env.VITE_API_URL) {
		return env.VITE_API_URL;
	}

	// In development with proxy enabled, use relative URLs
	if ((env.MODE === "development" || env.MODE === "test") && env.VITE_USE_PROXY) {
		return ""; // Relative URLs will be proxied by Vite
	}

	// In development without proxy, direct connection to backend
	if (env.MODE === "development" || env.MODE === "test") {
		return "http://127.0.0.1:8005"; // Direct connection to backend
	}

	// Production fallback
	return "http://localhost:8005";
};

// Application configuration
export const config = {
	api: {
		baseURL: getApiBaseUrl(),
		timeout: env.VITE_API_TIMEOUT || 30000,
		pluginInstallTimeout: env.VITE_PLUGIN_INSTALL_TIMEOUT || 120000,
	},
	auth: {
		tokenKey: "accessToken",
		development: {
			autoLogin: env.VITE_DEV_AUTO_LOGIN || false,
			email: env.VITE_DEV_EMAIL,
			password: env.VITE_DEV_PASSWORD,
		},
	},
	env: {
		isDevelopment: env.MODE === "development" || env.MODE === "test",
		isProduction: env.MODE === "production",
	},
	devMode: {
		pluginStudio: env.VITE_PLUGIN_STUDIO_DEV_MODE || false,
		showEditingControls: env.VITE_SHOW_EDITING_CONTROLS || false,
	},
} as const;

// Type exports
export type Config = typeof config;
