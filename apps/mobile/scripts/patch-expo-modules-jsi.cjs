'use strict';

const fs = require('node:fs');
const path = require('node:path');

const packageJsonPath = require.resolve('expo-modules-jsi/package.json');
const packageRoot = path.dirname(packageJsonPath);
const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf8'));

if (packageJson.version !== '57.0.4') {
  throw new Error(
    `Review the ExpoModulesJSI Xcode compatibility patch for expo-modules-jsi ${packageJson.version}.`,
  );
}

const sourcePath = path.join(
  packageRoot,
  'apple',
  'Sources',
  'ExpoModulesJSI',
  'Coding',
  'JavaScriptCodable+Date.swift',
);
const original =
  'guard milliseconds.isFinite, abs(milliseconds) <= maxJavaScriptDateMilliseconds else {';
const replacement =
  'guard milliseconds.isFinite, Swift.abs(milliseconds) <= maxJavaScriptDateMilliseconds else {';
const source = fs.readFileSync(sourcePath, 'utf8');

if (source.includes(replacement)) {
  console.log('ExpoModulesJSI Xcode compatibility patch is already applied.');
  process.exit(0);
}

const matches = source.split(original).length - 1;
if (matches !== 1) {
  throw new Error(
    `Expected exactly one ExpoModulesJSI date guard to patch, found ${matches}.`,
  );
}

fs.writeFileSync(sourcePath, source.replace(original, replacement));
console.log('Applied ExpoModulesJSI Xcode compatibility patch.');
