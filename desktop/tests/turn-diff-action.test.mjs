import test from 'node:test';
import assert from 'node:assert/strict';
import { turnDiffAction } from '../src/turnDiffAction.ts';

test('main receipt rows open each exact historical change rather than current disk', () => {
  const history = [], disk = [];
  for (const file of [{path:'one.txt',receiptId:'one'}, {path:'two.txt',receiptId:'two'}]) {
    const action = turnDiffAction(file, true, (...args)=>history.push(args), path=>disk.push(path));
    assert.equal(action.label, '历史变更'); action.open();
  }
  assert.deepEqual(history, [['one','one.txt'],['two','two.txt']]);
  assert.deepEqual(disk, []);
});
test('non-receipt browsing and absent history retain existing file preview; no callbacks disables action', () => {
  const file = {path:'one.txt',receiptId:'one'}, disk = [];
  const openFile = path=>disk.push(path);
  turnDiffAction(file, false, ()=>assert.fail('unexpected history'), openFile).open();
  const fallback = turnDiffAction(file, true, undefined, openFile);
  assert.equal(fallback.label, '预览'); fallback.open();
  assert.deepEqual(disk, ['one.txt','one.txt']);
  assert.equal(turnDiffAction(file, true).open, undefined);
});
test('history failure never silently substitutes current disk', () => {
  const action = turnDiffAction({path:'one.txt',receiptId:'one'}, true, ()=>{throw Error('missing receipt');}, ()=>assert.fail('disk fallback'));
  assert.throws(action.open, /missing receipt/);
});
