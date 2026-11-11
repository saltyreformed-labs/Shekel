/**
 * Shekel Budget App -- Signing In With a Passkey
 *
 * A passkey signs in on its own (plan#2538): this module asks the server for
 * a sign-in's options, has the device sign them when the person unlocks it
 * with Face ID, a fingerprint or its passcode, and posts the device's answer
 * in the sign-in form, which the server checks and answers with a redirect,
 * as the password form is answered.
 *
 * Two ways in, on the sign-in page:
 *
 *   - the email box's autofill: where the browser offers it (conditional
 *     mediation), a ceremony waits for the person to choose a passkey from
 *     the box's suggestions (the box carries autocomplete="username
 *     webauthn");
 *   - the "Sign in with a passkey" button, which stops that ceremony and
 *     shows the device's own sheet.
 *
 * Both use ONE set of options, fetched before either is needed: the server
 * keeps one ceremony per session, and Safari shows the passkey sheet only
 * when it is asked inside the person's tap, so the button cannot wait for a
 * fetch first.  The button is disabled while the options are fetched and
 * while its own ceremony is being answered, and nothing fetches new options
 * then: they would replace, on the server, the ceremony the person is
 * answering.  Each ceremony is given only what is left of its challenge's
 * life, so the device gives up before the server would refuse its answer.
 *
 * A challenge lives five minutes and a waiting autofill ceremony never gives
 * up of its own accord, so the options are fetched again once their timeout
 * has passed (or on return to a page that was hidden then).  The page's form
 * token lives an hour, so once a refresh is refused for it (a 400, which only
 * the form token's check answers there) the page reloads, which brings a new
 * token and new options.  So a page left open still signs in at the first
 * try.  What a refresh cannot help is a passkey that signed in on another
 * device since these options were fetched: a challenge older than its last
 * use is refused, once, and the retry has new options.
 *
 * WebAuthn wants bytes where its JSON carries unpadded base64url text, both
 * ways.  This module converts them itself rather than through
 * PublicKeyCredential.parseRequestOptionsFromJSON() and toJSON(), which not
 * every browser or passkey manager has.
 *
 * No inline script (CSP): the page says everything by data-* attributes on
 * the sign-in form:
 *
 *   <form data-passkey-sign-in data-options-url="...">
 *     <input name="csrf_token">       also sent with the options' request
 *     <input name="credential">       where the device's answer is written
 *     <button data-action="passkey-sign-in">
 *     <div data-passkey-message hidden>   where a plain message is shown
 */

(function () {
  "use strict";

  /** What the person is told, in plain words, when a sign-in cannot go on. */
  var MESSAGES = {
    unsupported: "This browser cannot use a passkey. " +
      "Please sign in with your email and password.",
    cancelled: "The passkey sign-in did not finish. " +
      "You can try again, or sign in with your email and password.",
    failed: "The passkey sign-in could not start. " +
      "Please try again, or sign in with your email and password."
  };

  /**
   * Return the bytes unpadded base64url *text* holds.
   *
   * @param {string} text  Base64url text, as WebAuthn's JSON writes binary.
   * @returns {ArrayBuffer} Its bytes.
   */
  function toBytes(text) {
    var base64 = text.replace(/-/g, "+").replace(/_/g, "/");
    while (base64.length % 4 !== 0) {
      base64 += "=";
    }
    var binary = window.atob(base64);
    var bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
  }

  /**
   * Return *buffer* as unpadded base64url text.
   *
   * @param {ArrayBuffer} buffer  Bytes the device returned.
   * @returns {string} Their base64url text, which the server reads.
   */
  function toText(buffer) {
    var bytes = new Uint8Array(buffer);
    var binary = "";
    for (let i = 0; i < bytes.length; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return window.btoa(binary)
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  /**
   * Return the options navigator.credentials.get() takes, from the server's JSON.
   *
   * @param {Object} json  The server's options: {publicKey: {...}}.
   * @returns {Object} The publicKey options, their binary members as bytes.
   */
  function requestOptions(json) {
    var publicKey = Object.assign({}, json.publicKey);
    publicKey.challenge = toBytes(publicKey.challenge);
    if (publicKey.allowCredentials) {
      publicKey.allowCredentials = publicKey.allowCredentials.map(
        function (descriptor) {
          return Object.assign({}, descriptor, { id: toBytes(descriptor.id) });
        }
      );
    }
    return publicKey;
  }

  /**
   * Return the device's answer as the JSON the server reads.
   *
   * Both ids are written from rawId, so they are the same text whatever the
   * browser put in id.  Shekel asks for no extension, so none is reported.
   *
   * @param {PublicKeyCredential} credential  What navigator.credentials.get()
   *   resolved to.
   * @returns {string} The answer's JSON.
   */
  function answerJSON(credential) {
    var response = credential.response;
    var id = toText(credential.rawId);
    var answer = {
      id: id,
      rawId: id,
      type: credential.type,
      response: {
        clientDataJSON: toText(response.clientDataJSON),
        authenticatorData: toText(response.authenticatorData),
        signature: toText(response.signature)
      },
      clientExtensionResults: {}
    };
    if (response.userHandle) {
      answer.response.userHandle = toText(response.userHandle);
    }
    return JSON.stringify(answer);
  }

  /**
   * Return whether this browser can use a passkey at all.
   *
   * @returns {boolean} True where WebAuthn's credentials API is present.
   */
  function supported() {
    return typeof window.PublicKeyCredential === "function" &&
      Boolean(navigator.credentials) &&
      typeof navigator.credentials.get === "function";
  }

  /**
   * Wire one sign-in form: its options, its waiting ceremony and its button.
   *
   * @param {HTMLFormElement} form  The form carrying data-passkey-sign-in.
   */
  function init(form) {
    var button = form.querySelector('[data-action="passkey-sign-in"]');
    var message = form.querySelector("[data-passkey-message]");
    var field = form.elements.namedItem("credential");
    var token = form.elements.namedItem("csrf_token").value;

    // The options in hand: {options, asked}, asked being when they were
    // requested, so their age is never under-counted.  null while none are.
    var ready = null;
    // The fetch of options in flight, so two callers share one.
    var fetching = null;
    // Whether options have ever arrived on this page: a refusal after that
    // is the page's form token lapsing, never a page that could not start.
    var arrived = false;
    // The waiting autofill ceremony's AbortController, or null.
    var waiting = null;
    // Whether the button's own ceremony is being answered.
    var asking = false;
    // Whether the browser offers passkeys from the email box's autofill.
    var autofill = false;
    // Set once an answer is posted: nothing begins after it.
    var submitted = false;

    function say(text) {
      message.textContent = text;
      message.hidden = false;
    }

    function age() {
      return Date.now() - ready.asked;
    }

    function stale() {
      return age() >= ready.options.publicKey.timeout;
    }

    function publicKeyNow() {
      // What is left of the challenge's life, so the device gives up before
      // the server would refuse its answer.
      var publicKey = requestOptions(ready.options);
      publicKey.timeout = Math.max(0, ready.options.publicKey.timeout - age());
      return publicKey;
    }

    function stopWaiting() {
      if (waiting !== null) {
        waiting.abort();
        waiting = null;
      }
    }

    function submit(credential) {
      submitted = true;
      stopWaiting();
      button.disabled = true;
      field.value = answerJSON(credential);
      form.submit();
    }

    function wait() {
      var controller = new AbortController();
      waiting = controller;
      navigator.credentials.get({
        publicKey: publicKeyNow(),
        mediation: "conditional",
        signal: controller.signal
      }).then(function (credential) {
        if (waiting === controller && credential) {
          submit(credential);
        }
      }, function () {
        // Stopped by this module, or refused: the person never asked for
        // this ceremony, so it says nothing, and the button still works.
        if (waiting === controller) {
          waiting = null;
        }
      });
    }

    function refresh() {
      // Never while the button's ceremony is answered: new options would
      // replace it on the server.  A fetch that failed is tried again too.
      if (submitted || asking || document.hidden) {
        return;
      }
      if (ready === null ? fetching === null : stale()) {
        prepare();
      }
    }

    function prepare() {
      if (fetching !== null || asking || submitted) {
        return;
      }
      stopWaiting();
      ready = null;
      button.disabled = true;
      var asked = Date.now();
      fetching = fetch(form.dataset.optionsUrl, {
        method: "POST",
        headers: { "X-CSRFToken": token, "Accept": "application/json" },
        credentials: "same-origin"
      }).then(function (response) {
        if (!response.ok) {
          if (response.status === 400 && arrived) {
            // The form token has lapsed: a reload brings a new one, and
            // the sign-in form would be refused for it too.
            window.location.reload();
          }
          throw new Error("passkey options answered " + response.status);
        }
        return response.json();
      }).then(function (options) {
        fetching = null;
        if (submitted) {
          return;
        }
        arrived = true;
        ready = { options: options, asked: asked };
        button.disabled = false;
        window.setTimeout(refresh, options.publicKey.timeout);
        if (autofill) {
          wait();
        }
      }, function () {
        // The button is left on: a tap says so and asks again.
        fetching = null;
        button.disabled = false;
      });
    }

    button.addEventListener("click", function () {
      message.hidden = true;
      if (!supported()) {
        say(MESSAGES.unsupported);
        return;
      }
      if (asking) {
        return;
      }
      if (ready === null || stale()) {
        say(MESSAGES.failed);
        prepare();
        return;
      }
      stopWaiting();
      asking = true;
      button.disabled = true;
      // Asked here, inside the tap, with nothing awaited first: Safari shows
      // the passkey sheet only in answer to the person's own gesture.
      navigator.credentials.get({ publicKey: publicKeyNow() })
        .then(function (credential) {
          if (!credential) {
            throw new Error("no passkey was chosen");
          }
          asking = false;
          submit(credential);
        })
        .catch(function (error) {
          asking = false;
          button.disabled = false;
          say(error && error.name === "NotAllowedError"
            ? MESSAGES.cancelled : MESSAGES.failed);
          // The same options serve the next try while they live: the
          // server's ceremony was never answered.
          if (stale()) {
            prepare();
          } else if (autofill) {
            wait();
          }
        });
    });

    if (!supported()) {
      return;
    }
    document.addEventListener("visibilitychange", refresh);
    var offered = typeof PublicKeyCredential.isConditionalMediationAvailable === "function"
      ? PublicKeyCredential.isConditionalMediationAvailable()
      : Promise.resolve(false);
    offered.then(function (available) {
      autofill = available === true;
    }, function () {
      autofill = false;
    }).then(prepare);
  }

  function initAll() {
    var forms = document.querySelectorAll("form[data-passkey-sign-in]");
    for (let i = 0; i < forms.length; i++) {
      init(forms[i]);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
  } else {
    initAll();
  }
})();
