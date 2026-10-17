import manifest from './schema.json';

/** Bumped whenever a node, mark or attribute changes; schema.json lists them.
 *  The server sends its own in `init`, and a client on another version stops
 *  before applying anything. */
export const SCHEMA_VERSION: number = manifest.version;
