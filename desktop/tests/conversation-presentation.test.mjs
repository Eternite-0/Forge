import test from 'node:test';
import assert from 'node:assert/strict';
import { visibleConversationMessage } from '../src/conversationPresentation.ts';
test('normal replies and clarification remain visible, explicit background commentary does not', () => {
  assert.equal(visibleConversationMessage({role:'user'}), true);
  assert.equal(visibleConversationMessage({role:'assistant', content:'你想使用哪个版本？'}), true);
  assert.equal(visibleConversationMessage({role:'assistant', metadata:{execution_mode:'mock'}}), true);
  assert.equal(visibleConversationMessage({role:'assistant', metadata:{source:'router_notice'}}), true);
  assert.equal(visibleConversationMessage({role:'assistant', metadata:{source:'router_notice', notice_phase:'review_pending'}}), false);
  assert.equal(visibleConversationMessage({role:'assistant', metadata:JSON.stringify({phase:'commentary'})}), false);
  assert.equal(visibleConversationMessage({role:'assistant', metadata:{channel:'commentary'}}), false);
  assert.equal(visibleConversationMessage({role:'assistant', metadata:'bad-json'}), true);
});
