import test from 'node:test';
import assert from 'node:assert/strict';
import { completionNoticeQuestId, visibleConversationMessage } from '../src/conversationPresentation.ts';
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
test('task navigation requires the exact Core fallback and a current workspace task', () => {
  const quests = [{id:'known'}];
  const metadata = {source:'router_notice', execution_mode:'programmatic', notice_phase:'completion_summary_unavailable', completion_execution_mode:'live', quest_id:'known'};
  const message = {role:'assistant', metadata};
  assert.equal(completionNoticeQuestId(message, quests), 'known');
  assert.equal(completionNoticeQuestId({...message, metadata:JSON.stringify(metadata)}, quests), 'known');
  assert.equal(completionNoticeQuestId(message, []), null);
  for (const [key, values] of Object.entries({source:[undefined,'model'], execution_mode:[undefined,'mock','live'], notice_phase:[undefined,'completed'], completion_execution_mode:[undefined,'mock','unknown'], quest_id:[undefined,'','unknown',' known']})) {
    for (const value of values) assert.equal(completionNoticeQuestId({...message, metadata:{...metadata,[key]:value}}, quests), null, `${key}=${value}`);
  }
  assert.equal(completionNoticeQuestId({...message,role:'user'}, quests), null);
  for (const value of ['bad-json', [], null]) assert.equal(completionNoticeQuestId({...message,metadata:value}, quests), null);
});
