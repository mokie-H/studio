import { mount } from '@vue/test-utils';
import ProgressModal from '../ProgressModal';
import store from '../../../store';

const task = { task: { id: 123, task_type: 'test-task' } };

function makeWrapper(computed = {}) {
  return mount(ProgressModal, {
    store,
    computed: {
      currentTask() {
        return task;
      },
      ...computed,
    },
  });
}

describe('progressModal', () => {
  it('should be hidden if there is no task', () => {
    let wrapper = makeWrapper({
      currentTask() {
        return null;
      },
    });
    expect(wrapper.find('[data-test="progressmodal"]').exists()).toBe(false);
  });
  it('should be hidden if the current task has noDialog set', () => {
    let wrapper = makeWrapper({
      currentTask() {
        return { noDialog: true };
      },
    });
    expect(wrapper.find('[data-test="progressmodal"]').exists()).toBe(false);
  });
  it('should show an error if the task failed', () => {
    let wrapper = makeWrapper({
      currentTaskError() {
        return { data: 'nope' };
      },
    });
    expect(wrapper.find('[data-test="error"]').exists()).toBe(true);
  });
  it('refresh button should be shown if task is done', () => {
    let wrapper = makeWrapper({
      progressPercent() {
        return 100;
      },
    });
    expect(wrapper.find('[data-test="refresh"]').exists()).toBe(true);
  });
  it('refresh button should be shown if task failed', () => {
    let wrapper = makeWrapper({
      currentTaskError() {
        return { data: 'uh-oh!' };
      },
    });
    expect(wrapper.find('[data-test="refresh"]').exists()).toBe(true);
  });
  it('refresh button should reload the page', () => {
    let deactivateTaskUpdateTimer = jest.fn();
    window.location.reload = jest.fn();
    let wrapper = makeWrapper({
      progressPercent() {
        return 100;
      },
    });
    wrapper.setMethods({ deactivateTaskUpdateTimer });
    wrapper.find('[data-test="refresh"]').trigger('click');
    expect(window.location.reload).toHaveBeenCalled();
    expect(deactivateTaskUpdateTimer).toHaveBeenCalled();
  });

  describe('on cancel task', () => {
    let wrapper;
    beforeEach(() => {
      wrapper = makeWrapper({
        progressPercent() {
          return 50;
        },
      });
    });
    it('stop task button should be shown if task is in progress', () => {
      expect(wrapper.find('[data-test="stop"]').exists()).toBe(true);
    });
    it('clicking stop button should switch window to confirmation window', () => {
      wrapper.find('[data-test="stop"]').trigger('click');
      expect(wrapper.vm.step).toBe(2);
    });
    it('clicking stop button on confirmation window should cancel the task', () => {
      let deleteCurrentTask = jest.fn();
      window.location.reload = jest.fn();
      wrapper.setMethods({ deleteCurrentTask });
      wrapper.setData({ step: 2 });
      wrapper.find('[data-test="confirmstop"]').trigger('click');
      expect(window.location.reload).toHaveBeenCalled();
      expect(deleteCurrentTask).toHaveBeenCalled();
    });
    it('clicking cancel button on confirmation window should go back to progress window', () => {
      let deleteCurrentTask = jest.fn();
      wrapper.setMethods({ deleteCurrentTask });
      wrapper.setData({ step: 2 });
      wrapper.find('[data-test="cancelstop"]').trigger('click');
      expect(wrapper.vm.step).toBe(1);
      expect(deleteCurrentTask).not.toHaveBeenCalled();
    });
  });
});
