# Copyright (c) 2020 Tulir Asokan
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
from typing import Any, Dict

from mautrix.types import (EncryptedOlmEventContent, EventType, UserID, DeviceID, OlmCiphertext,
                           EncryptionKeyAlgorithm)

from .base import BaseOlmMachine, verify_signature_json
from .types import DeviceIdentity, DecryptedOlmEvent, OlmEventKeys
from .sessions import Session


class OlmEncryptionMachine(BaseOlmMachine):
    async def _encrypt_olm_event(self, session: Session, recipient: DeviceIdentity,
                                 event_type: EventType, content: Any) -> EncryptedOlmEventContent:
        evt = DecryptedOlmEvent(sender=self.client.mxid, sender_device=self.client.device_id,
                                keys=OlmEventKeys(ed25519=self.account.signing_key),
                                recipient=recipient.user_id,
                                recipient_keys=OlmEventKeys(ed25519=recipient.signing_key),
                                type=event_type, content=content)
        ciphertext = session.encrypt(evt.json())
        await self.crypto_store.update_session(recipient.identity_key, session)
        return EncryptedOlmEventContent(ciphertext={recipient.identity_key: ciphertext},
                                        sender_key=self.account.identity_key)

    async def _create_outbound_sessions(self, users: Dict[UserID, Dict[DeviceID, DeviceIdentity]]
                                        ) -> None:
        request: Dict[UserID, Dict[DeviceID, EncryptionKeyAlgorithm]] = {}
        for user_id, devices in users.items():
            request[user_id] = {}
            for device_id, identity in devices.items():
                if not await self.crypto_store.has_session(identity.identity_key):
                    request[user_id][device_id] = EncryptionKeyAlgorithm.SIGNED_CURVE25519
            if not request[user_id]:
                del request[user_id]
        if not request:
            return
        keys = await self.client.claim_keys(request)
        for user_id, devices in keys.one_time_keys.items():
            for device_id, one_time_keys in devices.items():
                key_id, one_time_key_data = one_time_keys.popitem()
                one_time_key = one_time_key_data["key"]
                identity = users[user_id][device_id]
                if not verify_signature_json(one_time_key_data, user_id, device_id,
                                             identity.signing_key):
                    self.log.warning(f"Invalid signature for {device_id} of {user_id}")
                else:
                    session = self.account.new_outbound_session(identity.identity_key, one_time_key)
                    await self.crypto_store.add_session(identity.identity_key, session)