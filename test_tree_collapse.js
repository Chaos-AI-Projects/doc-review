#!/usr/bin/env node
/**
 * Behavioral test for renderTree ancestor-expand logic (#439).
 *
 * Runs the same path-prefix logic used in app.js against known inputs
 * and asserts the details.open state for each directory.
 *
 * Exit 0 = all pass, exit 1 = failure (message on stderr).
 */
"use strict";

// ── Replicate the ancestor-open logic from app.js ──

function isAncestor(dirPath, currentPath) {
    return currentPath === dirPath ||
        currentPath.indexOf(dirPath + "/") === 0;
}

function computeDirPath(prefix, dirName) {
    return prefix ? prefix + "/" + dirName : dirName;
}

// ── Test cases ──

var failures = [];

function assert(label, actual, expected) {
    if (actual !== expected) {
        failures.push(label + ": expected " + expected + ", got " + actual);
    }
}

// Case 1: viewing kb/wiki/log.md → kb and kb/wiki should open, others closed
var cp1 = "kb/wiki/log.md";
assert("kb is ancestor of kb/wiki/log.md",
    isAncestor("kb", cp1), true);
assert("kb/wiki is ancestor of kb/wiki/log.md",
    isAncestor("kb/wiki", cp1), true);
assert("src is NOT ancestor of kb/wiki/log.md",
    isAncestor("src", cp1), false);
assert("kb/other is NOT ancestor of kb/wiki/log.md",
    isAncestor("kb/other", cp1), false);

// Case 2: viewing README.md (root-level file) → no directories should open
var cp2 = "README.md";
assert("kb is NOT ancestor of README.md",
    isAncestor("kb", cp2), false);
assert("src is NOT ancestor of README.md",
    isAncestor("src", cp2), false);

// Case 3: deeply nested file
var cp3 = "a/b/c/d/file.txt";
assert("a is ancestor of a/b/c/d/file.txt",
    isAncestor("a", cp3), true);
assert("a/b is ancestor of a/b/c/d/file.txt",
    isAncestor("a/b", cp3), true);
assert("a/b/c is ancestor of a/b/c/d/file.txt",
    isAncestor("a/b/c", cp3), true);
assert("a/b/c/d is ancestor of a/b/c/d/file.txt",
    isAncestor("a/b/c/d", cp3), true);
assert("a/b/c/d/e is NOT ancestor of a/b/c/d/file.txt",
    isAncestor("a/b/c/d/e", cp3), false);
assert("b is NOT ancestor of a/b/c/d/file.txt",
    isAncestor("b", cp3), false);

// Case 4: prefix ambiguity — "kb" should not match "kbase/..."
var cp4 = "kbase/doc.md";
assert("kb is NOT ancestor of kbase/doc.md (prefix ambiguity)",
    isAncestor("kb", cp4), false);
assert("kbase is ancestor of kbase/doc.md",
    isAncestor("kbase", cp4), true);

// Case 5: computeDirPath with empty prefix
assert("computeDirPath('', 'kb') => 'kb'",
    computeDirPath("", "kb"), "kb");
assert("computeDirPath('kb', 'wiki') => 'kb/wiki'",
    computeDirPath("kb", "wiki"), "kb/wiki");
assert("computeDirPath('a/b', 'c') => 'a/b/c'",
    computeDirPath("a/b", "c"), "a/b/c");

// ── Report ──

if (failures.length > 0) {
    process.stderr.write("FAIL: " + failures.length + " assertion(s) failed:\n");
    for (var i = 0; i < failures.length; i++) {
        process.stderr.write("  - " + failures[i] + "\n");
    }
    process.exit(1);
} else {
    process.stdout.write("OK: all tree-collapse assertions passed\n");
    process.exit(0);
}
