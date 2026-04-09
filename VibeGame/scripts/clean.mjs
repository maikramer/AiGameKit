#!/usr/bin/env node
import fs from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
fs.rmSync(join(root, 'dist'), { recursive: true, force: true });
