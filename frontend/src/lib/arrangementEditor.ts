/**
 * Monaco is loaded from jsDelivr when the arrangement page opens.
 * It is not part of the canister bundle: one editor file is larger than the
 * 2 MiB inter-canister limit, and the full editor is hundreds of files.
 */
const MONACO_VS = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.57.0/min/vs';

type MonacoNs = {
  editor: {
    create: (
      host: HTMLElement,
      options: Record<string, unknown>,
    ) => {
      dispose: () => void;
      layout: () => void;
      getValue: () => string;
      setValue: (value: string) => void;
      updateOptions: (options: Record<string, unknown>) => void;
      onDidChangeModelContent: (listener: () => void) => void;
    };
  };
  languages: {
    json?: {
      jsonDefaults: { setDiagnosticsOptions: (options: object) => void };
    };
  };
};

type AmdRequire = {
  config: (options: { paths: Record<string, string> }) => void;
  (
    deps: string[],
    onLoad: (monaco: MonacoNs) => void,
    onError?: (err: unknown) => void,
  ): void;
};

let loading: Promise<MonacoNs> | null = null;

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing) {
      resolve();
      return;
    }
    const script = document.createElement('script');
    script.src = src;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`Could not download the code editor from ${src}`));
    document.head.appendChild(script);
  });
}

function loadMonaco(): Promise<MonacoNs> {
  if (!loading) {
    loading = (async () => {
      await loadScript(`${MONACO_VS}/loader.js`);
      const req = (globalThis as { require?: AmdRequire }).require;
      if (!req) throw new Error('The Monaco loader did not start');
      req.config({ paths: { vs: MONACO_VS } });
      return await new Promise<MonacoNs>((resolve, reject) => {
        req(['vs/editor/editor.main'], resolve, reject);
      });
    })();
  }
  return loading;
}

/** Light editor matching the Casals page (`vs` is Monaco's white theme). */
export async function createArrangementEditor(host: HTMLElement, initial: string, readOnly: boolean) {
  const monaco = await loadMonaco();
  monaco.languages.json?.jsonDefaults.setDiagnosticsOptions({
    validate: true,
    allowComments: false,
    schemas: [
      {
        uri: 'inmemory://casals/arrangement.json',
        fileMatch: ['*'],
        schema: {
          type: 'object',
          required: ['stand_template'],
          additionalProperties: false,
          properties: {
            stand_template: { type: 'object' },
          },
        },
      },
    ],
  });
  return monaco.editor.create(host, {
    value: initial,
    language: 'json',
    theme: 'vs',
    readOnly,
    automaticLayout: true,
    minimap: { enabled: true },
    fontSize: 13,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    scrollBeyondLastLine: false,
    padding: { top: 12, bottom: 12 },
    renderLineHighlight: 'line',
    smoothScrolling: true,
    wordWrap: 'on',
  });
}
