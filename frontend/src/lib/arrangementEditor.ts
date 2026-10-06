/**
 * Monaco ships in the Vite bundle. Workers are same-origin `?worker` chunks,
 * so the arrangement page does not fetch the editor from a CDN.
 */
import * as monaco from 'monaco-editor/editor/editor.api.js';
import 'monaco-editor/internal/common/workers.js';
import { jsonDefaults } from 'monaco-editor/language/json/monaco.contribution.js';
import editorWorker from 'monaco-editor/editor/editor.worker.js?worker';
import jsonWorker from 'monaco-editor/language/json/json.worker.js?worker';

globalThis.MonacoEnvironment = {
  getWorker(_workerId: string, label: string) {
    if (label === 'json') return new jsonWorker();
    return new editorWorker();
  },
};

/** Light editor matching the Casals page (`vs` is Monaco's white theme). */
export function createArrangementEditor(host: HTMLElement, initial: string, readOnly: boolean) {
  jsonDefaults.setDiagnosticsOptions({
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
