declare module 'monaco-editor/language/json/monaco.contribution.js' {
  export const jsonDefaults: {
    setDiagnosticsOptions(options: {
      validate?: boolean;
      allowComments?: boolean;
      schemas?: {
        uri: string;
        fileMatch?: string[];
        schema?: object;
      }[];
    }): void;
  };
}

declare const __BUILD_VERSION__: string;
declare const __BUILD_COMMIT__: string;
declare const __BUILD_TIME__: string;
/** Wall clock when this frontend was built to ship. Not the commit time. */
declare const __BUILD_DEPLOYED__: string;
